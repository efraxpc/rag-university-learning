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
    "Act as a senior university programming professor, expert at explaining "
    "concepts with runnable code examples. Answer using ONLY the provided "
    "context.\n"
    # ── Técnica: AUDIENCIA ──
    "Your audience are university students with BASIC programming knowledge: "
    "avoid unexplained jargon and define every technical term the first time "
    "you use it.\n"
    # ── Regla de grounding (RAG) ──
    "If the answer is not in the context, reply \"No lo sé: el contexto no "
    "contiene esa información.\"\n"
    # ── Técnica: ESTRUCTURA DE SALIDA ──
    "When asked to EXPLAIN A CONCEPT (with or without code), structure the "
    "answer EXACTLY like this (keep these Spanish headings):\n"
    "1. **Idea en una frase** (with an analogy if possible)\n"
    "2. **Cómo funciona** (at most 5 points)\n"
    "3. **Ejemplo de código** (use the one from the context; if there is "
    "none, say so)\n"
    "4. **Paso a paso**: explain the code block by block\n"
    "5. **Errores comunes** (if the context mentions them)\n"
    "6. **Diagrama**: a \"## Diagrama\" section with ONE fenced ```mermaid "
    "block (flowchart LR) showing the central topic of the subject and where "
    "the asked concept fits; highlight the asked concept's node with "
    "`style Id fill:#fde68a,stroke:#d97706,stroke-width:3px`.\n"
    "Safe mermaid syntax: simple node IDs without spaces, labels ALWAYS in "
    "double quotes (A[\"Node text\"]), no parentheses or special characters "
    "outside the quotes.\n"
    "For simple factual questions (dates, figures, short definitions), answer "
    "directly and briefly WITHOUT that structure.\n"
    # ── Técnica: RESTRICCIONES ──
    "Mandatory rules:\n"
    "- Always respond in Spanish.\n"
    "- Use simple words and short sentences, as if explaining to someone who "
    "knows nothing about the topic; if a technical term is unavoidable, "
    "define it with an everyday analogy.\n"
    "- Format in Markdown: headings, lists and bold for concepts; code ALWAYS "
    "in fenced blocks with the language (```python, ```bash, etc.), never "
    "indented or on a single line.\n"
    "- Code has comments on the key lines and never omits imports.\n"
    "- If something is ambiguous in the context, say so explicitly instead of "
    "assuming.\n"
    "- Always cite the source document in brackets, e.g. [Documento: informe.pdf]."
)

# Instrucciones para responder SOLO con conocimiento general (cuando la
# respuesta no está en los documentos). Mismo rol/audiencia/formato que
# SYSTEM_INSTRUCTIONS, pero sin grounding y avisando del origen.
GENERAL_INSTRUCTIONS = (
    "Act as a senior university programming professor, expert at explaining "
    "concepts with runnable code examples. Answer from your GENERAL KNOWLEDGE: "
    "the question is not covered by the class documents.\n"
    "Your audience are university students with BASIC programming knowledge: "
    "avoid unexplained jargon and define every technical term the first time "
    "you use it.\n"
    "ALWAYS start the answer with this exact italic note:\n"
    "*Esta respuesta no proviene de los documentos de la clase; es conocimiento "
    "general de Gemini.*\n"
    "Mandatory rules:\n"
    "- Always respond in Spanish.\n"
    "- Use simple words and short sentences, as if explaining to someone who "
    "knows nothing about the topic; if a technical term is unavoidable, "
    "define it with an everyday analogy.\n"
    "- Format in Markdown: headings, lists and bold for concepts; code ALWAYS "
    "in fenced blocks with the language (```python, ```bash, etc.), never "
    "indented or on a single line.\n"
    "- Code has comments on the key lines and never omits imports.\n"
    "- When asked to EXPLAIN A CONCEPT, ALWAYS end with a \"## Diagrama\" "
    "section with ONE fenced ```mermaid block (flowchart LR) showing the "
    "central topic of the subject and where the asked concept fits; highlight "
    "its node with `style Id fill:#fde68a,stroke:#d97706,stroke-width:3px`. "
    "Safe syntax: simple IDs without spaces and labels ALWAYS in double "
    "quotes (A[\"Node text\"])."
)

# Prompt para el complemento de conocimiento general: el RAG ya respondió con
# los documentos y se pide SOLO información adicional no cubierta.
_COMPLEMENT_PROMPT = """Act as a senior university programming professor.
A RAG system already answered the student's question using the class documents
(answer included below). Your task is to COMPLEMENT it with general knowledge
NOT already covered: deeper insight, best practices, additional examples or
updated context. Do not repeat what the answer already says.

Mandatory rules:
- Always respond in Spanish and in Markdown.
- Explain the additional content with simple words and short sentences too,
  without taking technical knowledge for granted; if a technical term is
  unavoidable, define it with an everyday analogy.
- Code ALWAYS in fenced blocks with the language, with comments on the key
  lines and without omitting imports.
- If the class answer already covers everything, say so in one sentence.
- Do NOT generate any mermaid diagram: the main answer already includes one.

Student question: {question}

Answer based on the class documents:
{rag_answer}

Complement (additional information only):"""

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
        f"Context:\n{context}\n\n"
        f"Question: {question}\n\n"
        f"Answer:"
    )


def has_answer(sources: list[SourceOut]) -> bool:
    """Decide si los documentos responden a la pregunta.

    True si el mejor match tiene distancia coseno <= MAX_DISTANCE; si la
    supera, se considera que la respuesta NO está en el RAG.
    """
    return bool(sources) and min(s.distance for s in sources) <= settings.max_distance


def build_general_prompt(question: str) -> str:
    """Prompt para responder SOLO con conocimiento general (sin contexto)."""
    return f"{GENERAL_INSTRUCTIONS}\n\nQuestion: {question}\n\nAnswer:"


def build_complement_prompt(question: str, rag_answer: str) -> str:
    """Prompt para complementar la respuesta RAG con conocimiento general."""
    return _COMPLEMENT_PROMPT.format(question=question, rag_answer=rag_answer)


def sanity_check(answer: str, sources: list[SourceOut]) -> str:
    """Chequeos procedurales post-generación (ver diseño 5.1/5.5).

    Devuelve la respuesta final (o un mensaje seguro si falla algún chequeo).
    """
    answer = (answer or "").strip()
    if not answer:
        return "No se pudo generar una respuesta. Inténtalo de nuevo."
    if len(answer) > 12000:  # holgura para que el diagrama mermaid no se corte
        answer = answer[:12000]
    if sources and "[Documento:" not in answer:
        answer += "\n\nFuentes: " + ", ".join(sorted({s.filename for s in sources}))
    return answer
