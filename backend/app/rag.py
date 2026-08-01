"""Lógica RAG del flujo de consulta: optimizaciones pre-retrieval
(query rewriting + query expansion multi-query), búsqueda híbrida con
fusión por parent, prompt augmentation y sanity checks procedurales."""

import logging

from sqlalchemy import text

from . import gemini
from .config import settings
from .db import get_engine
from .schemas import SourceOut

logger = logging.getLogger("rag.retrieval")

SYSTEM_INSTRUCTIONS = (
    # ── Técnica: ROLE PROMPTING ──
    "Actúa como profesor universitario senior de programación, experto en "
    "explicar conceptos con ejemplos de código ejecutables. Responde usando "
    "ÚNICAMENTE el contexto proporcionado.\n"
    # ── Técnica: AUDIENCIA ──
    "Tu audiencia son estudiantes universitarios con conocimientos BÁSICOS de "
    "programación: evita jerga sin explicar y define cada término técnico la "
    "primera vez que lo uses.\n"
    # ── Regla de grounding (RAG) ──
    "Si la respuesta no está en el contexto, responde \"No lo sé: el contexto "
    "no contiene esa información.\"\n"
    # ── Técnica: ESTRUCTURA DE SALIDA ──
    "Cuando te pidan EXPLICAR UN CONCEPTO (con o sin código), estructura la "
    "respuesta EXACTAMENTE así:\n"
    "1. **Idea en una frase** (con una analogía si es posible)\n"
    "2. **Cómo funciona** (máximo 5 puntos)\n"
    "3. **Ejemplo de código** (usa el del contexto; si no hay, dilo)\n"
    "4. **Paso a paso**: explica el código bloque por bloque\n"
    "5. **Errores comunes** (si el contexto los menciona)\n"
    "Para preguntas factuales simples (fechas, cifras, definiciones cortas), "
    "responde de forma directa y breve SIN esa estructura.\n"
    # ── Técnica: RESTRICCIONES ──
    "Restricciones obligatorias:\n"
    "- Responde siempre en español.\n"
    "- Formatea en Markdown: encabezados, listas y negritas para los conceptos; "
    "el código SIEMPRE en bloques fenced indicando el lenguaje (```python, "
    "```bash, etc.), nunca indentado ni en una sola línea.\n"
    "- El código lleva comentarios en las líneas clave y no omite imports.\n"
    "- Si algo es ambiguo en el contexto, dilo explícitamente en vez de asumirlo.\n"
    "- Cita siempre el documento de origen entre corchetes, p. ej. [Documento: informe.pdf]."
)

# Query unificada small-to-big y modo clásico:
# - SMALL_TO_BIG: los chunks pequeños se usan para buscar (precisión) pero se
#   devuelve el contenido del parent (contexto grande) via LEFT JOIN + COALESCE.
# - Modo clásico: parent_id es NULL y COALESCE cae en el propio chunk.
# DISTINCT ON escoge el mejor chunk por parent; la subquery reordena por distancia.
_SEARCH_SQL = text(
    """
    SELECT * FROM (
        SELECT DISTINCT ON (COALESCE(p.id, c.id))
               c.document_id, d.filename,
               COALESCE(p.content, c.content) AS content,
               c.embedding <=> CAST(:qvec AS vector) AS distance
        FROM chunks c
        LEFT JOIN parents p ON p.id = c.parent_id
        JOIN documents d ON d.id = c.document_id
        WHERE CAST(:doc_id AS INTEGER) IS NULL OR c.document_id = CAST(:doc_id AS INTEGER)
        ORDER BY COALESCE(p.id, c.id), c.embedding <=> CAST(:qvec AS vector)
    ) best_per_parent
    ORDER BY distance
    LIMIT :k
    """
)


def _candidate_queries(question: str) -> list[str]:
    """Query rewriting + query expansion (multi-query).

    Según los flags: ambas ON → [reescrita] + variantes; solo rewrite →
    [reescrita]; solo expansion → [original] + variantes; ambas OFF → [original].
    """
    if not (settings.query_rewrite or settings.query_expansion):
        return [question]
    result = gemini.rewrite_and_expand(question, settings.expansion_variants)
    if settings.query_rewrite and settings.query_expansion:
        queries = [result["rewritten"], *result["variants"]]
    elif settings.query_rewrite:
        queries = [result["rewritten"]]
    else:
        queries = [question, *result["variants"]]
    # Dedupe preservando el orden
    queries = list(dict.fromkeys(q for q in queries if q.strip()))
    logger.info(
        "query opt: original=%r → %d consultas candidatas: %s",
        question, len(queries), queries,
    )
    return queries


def _search_one(conn, qvec: str, document_id: int | None) -> list[SourceOut]:
    rows = conn.execute(
        _SEARCH_SQL, {"qvec": qvec, "doc_id": document_id, "k": settings.top_k}
    ).mappings()
    return [SourceOut(**row) for row in rows]


def hybrid_search(question: str, document_id: int | None) -> list[SourceOut]:
    """Búsqueda semántica top-k (coseno, índice HNSW) + filtro por metadatos.

    - Pre-retrieval: rewriting + multi-query expansion (según flags).
    - Small-to-big: devuelve el contenido del parent aunque el match sea en un child.
    - Fusión multi-query: unión por parent quedándose con la mínima distancia.
    """
    queries = _candidate_queries(question)
    vectors = gemini.embed_texts(queries, task_type="RETRIEVAL_QUERY")

    best: dict[tuple[int, str], SourceOut] = {}
    with get_engine().connect() as conn:
        for query, vec in zip(queries, vectors):
            for src in _search_one(conn, gemini.vector_to_literal(vec), document_id):
                key = (src.document_id, src.content)  # parent = clave de fusión
                if key not in best or src.distance < best[key].distance:
                    best[key] = src

    merged = sorted(best.values(), key=lambda s: s.distance)[: settings.top_k]
    logger.info(
        "fusión: %d candidatos únicos → top-%d (distancias: %s)",
        len(best), len(merged), [round(s.distance, 4) for s in merged],
    )
    return merged


def build_prompt(question: str, sources: list[SourceOut]) -> str:
    context = "\n\n".join(
        f"[Documento: {s.filename}]\n{s.content}" for s in sources
    )
    return (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"Contexto:\n{context}\n\n"
        f"Pregunta: {question}\n\n"
        f"Respuesta:"
    )


def sanity_check(answer: str, sources: list[SourceOut]) -> str:
    """Chequeos procedurales post-generación (ver diseño 5.1/5.5).

    Devuelve la respuesta final (o un mensaje seguro si falla algún chequeo).
    """
    answer = (answer or "").strip()
    if not answer:
        return "No se pudo generar una respuesta. Inténtalo de nuevo."
    if len(answer) > 8000:
        answer = answer[:8000]
    if sources and "[Documento:" not in answer:
        answer += "\n\nFuentes: " + ", ".join(sorted({s.filename for s in sources}))
    return answer
