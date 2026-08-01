"""Lógica RAG del flujo de consulta: optimizaciones pre-retrieval
(query rewriting + query expansion multi-query), búsqueda híbrida con
fusión por parent, prompt augmentation y sanity checks procedurales.
Incluye el resumen de clase entera por metadatos (map-reduce + caché)."""

import logging
import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text

from . import gemini, llm
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
    "general de Claude.*\n"
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
    result = llm.rewrite_and_expand(question, settings.expansion_variants)
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


# ── Resumen de clase entera (por metadatos, no por vectores) ──
# La búsqueda top-k nunca cubre un documento completo: para resumirlo se
# traen TODOS sus bloques ordenados por metadatos (parent_index/chunk_index)
# y se aplica map-reduce (resumir cada grupo en paralelo y combinar). El
# resultado se cachea en documents.summary: solo la primera petición paga
# el coste — clave para corpus extremadamente largos.

# Mismo patrón small-to-big que _SEARCH_SQL: DISTINCT ON evita duplicar el
# parent por cada child; en modo clásico (parent_id NULL) cae en el chunk.
_DOC_BLOCKS_SQL = text(
    """
    SELECT DISTINCT ON (COALESCE(p.id, c.id))
           COALESCE(p.parent_index, c.chunk_index) AS orden,
           COALESCE(p.content, c.content)          AS content
    FROM chunks c
    LEFT JOIN parents p ON p.id = c.parent_id
    WHERE c.document_id = :doc_id
    ORDER BY COALESCE(p.id, c.id), c.chunk_index
    """
)

# Detección de petición de resumen por keywords (conservadora).
_SUMMARY_RE = re.compile(
    r"\bres\w*m\w*\b"           # resume, resumen, resúmeme, resumir...
    r"|\bsinteti\w*\b|\bsumario\b"
    r"|\bde qu[ée] (trata|va)\b"
    r"|\bidea general\b|\bpuntos (clave|principales)\b",
    re.IGNORECASE,
)

SUMMARY_INSTRUCTIONS = (
    "Act as a senior university programming professor summarizing a class "
    "document for students with BASIC programming knowledge: avoid "
    "unexplained jargon and define every technical term the first time you "
    "use it, with an everyday analogy if possible.\n"
    "Write a STRUCTURED SUMMARY in Spanish and Markdown with EXACTLY these "
    "headings:\n"
    "1. **Idea general** (2-3 frases)\n"
    "2. **Temas tratados** (lista)\n"
    "3. **Puntos clave por tema** (bullets con negrita para los conceptos)\n"
    "4. **Conclusiones**\n"
    "Mandatory rules:\n"
    "- Use ONLY the provided content: do not add outside knowledge.\n"
    "- Short sentences and simple words.\n"
    "- If the content looks like only a PART of the document, summarize just "
    "that part without inventing a global structure.\n"
    "- Cite the source document in brackets, e.g. [Documento: clase.pdf]."
)

_REDUCE_PROMPT = """Act as a senior university programming professor.
Below are partial summaries of consecutive sections of the SAME class
document. Merge them into ONE structured summary in Spanish and Markdown
with EXACTLY these headings:
1. **Idea general** (2-3 frases)
2. **Temas tratados** (lista)
3. **Puntos clave por tema** (bullets con negrita para los conceptos; define
   cada término técnico con palabras simples)
4. **Conclusiones**

Mandatory rules:
- Remove duplicates and keep a logical order; do not add outside knowledge.
- Short sentences and simple words, for students with basic knowledge.
- Cite the source document in brackets, e.g. [Documento: {filename}].

Document: {filename}

Partial summaries:
{partials}

Unified summary:"""

_MULTI_REDUCE_PROMPT = """Act as a senior university programming professor.
Below are summaries of DIFFERENT class documents selected by the student.
Merge them into ONE structured summary in Spanish and Markdown with EXACTLY
these headings:
1. **Idea general** (2-3 frases)
2. **Temas tratados** (lista)
3. **Puntos clave por tema** (bullets con negrita para los conceptos; define
   cada término técnico con palabras simples)
4. **Conclusiones**

Mandatory rules:
- Remove duplicates and keep a logical order; do not add outside knowledge.
- Short sentences and simple words, for students with basic knowledge.
- Cite the source document of each point in brackets, e.g.
  [Documento: clase1.pdf].
- If two documents cover the same topic, merge the content and cite both.

Summaries per document:
{items}

Unified summary:"""


def is_summary_request(question: str) -> bool:
    """True si la pregunta parece pedir un resumen del documento/clase."""
    return bool(_SUMMARY_RE.search(question))


def fetch_document_blocks(document_id: int) -> list[str]:
    """Contenido COMPLETO del documento en orden, usando solo metadatos
    (sin embeddings): parents en modo small-to-big, chunks en modo clásico.

    Lanza LookupError si el documento no existe o aún no tiene chunks.
    """
    with get_engine().connect() as conn:
        rows = conn.execute(_DOC_BLOCKS_SQL, {"doc_id": document_id}).mappings()
    blocks = [r["content"] for r in sorted(rows, key=lambda r: r["orden"])]
    if not blocks:
        raise LookupError(f"documento {document_id} sin contenido indexado")
    return blocks


def resolve_summary_document(
    question: str, document_id: int | None
) -> tuple[int, str] | None:
    """Resuelve QUÉ documento resumir usando metadatos, en este orden:

    1. document_id explícito (botón del chat) — debe existir y estar ready.
    2. Filename mencionado en la pregunta (match case-insensitive).
    3. Documento ready más reciente ("resume el último documento que subí").

    Devuelve (id, filename) o None si no hay candidato.
    """
    with get_engine().connect() as conn:
        if document_id is not None:
            row = conn.execute(
                text(
                    "SELECT id, filename FROM documents"
                    " WHERE id = :id AND status = 'ready'"
                ),
                {"id": document_id},
            ).mappings().first()
            return (row["id"], row["filename"]) if row else None
        rows = list(
            conn.execute(
                text(
                    "SELECT id, filename FROM documents WHERE status = 'ready'"
                    " ORDER BY created_at DESC"
                )
            ).mappings()
        )
    q = question.lower()
    for row in rows:
        if row["filename"].lower() in q:
            return row["id"], row["filename"]
    if rows:
        return rows[0]["id"], rows[0]["filename"]
    return None


def build_summary_prompt(filename: str, content: str) -> str:
    """Prompt del map: resumir un bloque (o el documento corto completo)."""
    return (
        f"{SUMMARY_INSTRUCTIONS}\n\n"
        f"Content of the class document ({filename}):\n{content}\n\n"
        f"Summary:"
    )


_TITLE_PROMPT = """Generate a SHORT title (max 10 words) for this class document,
as it would appear in a course classroom list.

Rules:
- Reply in SPANISH with ONLY the title: no quotes, no trailing period, no
  explanation.
- Base it ONLY on the provided content (topic of the class), not on the
  filename.

Filename (only as a hint): {filename}

Content (beginning of the document):
{content}

Title:"""

# Caracteres del inicio del documento que bastan para inferir el título.
_TITLE_CONTENT_CHARS = 6000


def build_title_prompt(filename: str, content: str) -> str:
    """Prompt del título auto-generado de la clase (documents.title)."""
    return _TITLE_PROMPT.format(filename=filename, content=content)


def generate_title(document_id: int) -> str:
    """Genera y cachea el título de la clase (documents.title).

    Usa el inicio del contenido (fetch_document_blocks, sin embeddings) y
    FAST_MODEL: una sola llamada barata por documento, ever. Lanza
    LookupError si el documento no tiene contenido.
    """
    blocks = fetch_document_blocks(document_id)
    with get_engine().connect() as conn:
        filename = conn.execute(
            text("SELECT filename FROM documents WHERE id = :id"),
            {"id": document_id},
        ).scalar_one()
    raw = llm.generate(
        build_title_prompt(filename, blocks[0][:_TITLE_CONTENT_CHARS]),
        model=settings.fast_model,
    )
    # Saneado: una sola línea, sin comillas ni punto final, largo acotado.
    title = raw.strip().splitlines()[0].strip().strip('"\'').rstrip(".")[:120]
    if not title:
        raise ValueError(f"el LLM devolvió un título vacío para el documento {document_id}")
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE documents SET title = :t WHERE id = :id"),
            {"t": title, "id": document_id},
        )
    logger.info("título generado para documento %d: %s", document_id, title)
    return title


def build_reduce_prompt(filename: str, partials: list[str]) -> str:
    """Prompt del reduce: combinar los resúmenes parciales en uno final."""
    joined = "\n\n---\n\n".join(
        f"Part {i}:\n{p}" for i, p in enumerate(partials, 1)
    )
    return _REDUCE_PROMPT.format(filename=filename, partials=joined)


def summarize_document(document_id: int) -> str:
    """Resumen de la clase entera por metadatos con map-reduce.

    Sirve la caché de documents.summary si existe; si no, trae el documento
    completo ordenado, resume grupos de bloques en paralelo (map), combina
    (reduce) y cachea el resultado. Lanza LookupError si el documento no
    existe o no tiene contenido.
    """
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT filename, summary FROM documents WHERE id = :id"),
            {"id": document_id},
        ).mappings().first()
    if row is None:
        raise LookupError(f"documento {document_id} no existe")
    if row["summary"]:
        logger.info("resumen de documento %d servido desde caché", document_id)
        return row["summary"]

    blocks = fetch_document_blocks(document_id)

    # Agrupar bloques enteros (sin cortar un parent/chunk por la mitad)
    # hasta el presupuesto de caracteres por llamada del map.
    groups: list[str] = []
    current: list[str] = []
    size = 0
    for block in blocks:
        if current and size + len(block) > settings.summary_block_chars:
            groups.append("\n\n".join(current))
            current, size = [], 0
        current.append(block)
        size += len(block)
    if current:
        groups.append("\n\n".join(current))

    logger.info(
        "resumen documento %d: %d bloques → %d grupos de map",
        document_id, len(blocks), len(groups),
    )

    if len(groups) == 1:
        summary = llm.generate(build_summary_prompt(row["filename"], groups[0]))
    else:
        # Map con FAST_MODEL (volumen barato); reduce con GEN_MODEL (calidad).
        with ThreadPoolExecutor(max_workers=settings.summary_max_workers) as pool:
            partials = list(
                pool.map(
                    lambda g: llm.generate(
                        build_summary_prompt(row["filename"], g),
                        model=settings.fast_model,
                    ),
                    groups,
                )
            )
        summary = llm.generate(build_reduce_prompt(row["filename"], partials))

    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE documents SET summary = :s WHERE id = :id"),
            {"s": summary, "id": document_id},
        )
    return summary


def build_multi_reduce_prompt(items: list[tuple[str, str]]) -> str:
    """Prompt del reduce multi-documento: fusionar los resúmenes de varias
    clases seleccionadas en uno solo, citando cada fuente."""
    joined = "\n\n---\n\n".join(
        f"Document: {filename}\nSummary:\n{summary}" for filename, summary in items
    )
    return _MULTI_REDUCE_PROMPT.format(items=joined)


def summarize_documents(document_ids: list[int]) -> str:
    """Resumen de VARIAS clases seleccionadas por el usuario (botón del chat
    con checkboxes / opción "todas").

    Estrategia: cada documento se resume con `summarize_document` (reutiliza
    la caché de documents.summary) y, si hay más de uno, un reduce final con
    GEN_MODEL fusiona los resúmenes citando cada fuente. El resumen combinado
    NO se cachea: es una sola llamada sobre resúmenes ya cacheados.

    Lanza LookupError si la selección está vacía o algún documento no existe
    o no está listo.
    """
    # Dedupe preservando el orden de selección.
    ids = list(dict.fromkeys(document_ids))
    if not ids:
        raise LookupError("selección de documentos vacía")
    if len(ids) == 1:
        return summarize_document(ids[0])

    # Validar que todos existen y están ready antes de gastar llamadas,
    # y obtener los filenames para las citas en la misma consulta.
    with get_engine().connect() as conn:
        rows = list(
            conn.execute(
                text("SELECT id, filename FROM documents WHERE status = 'ready'"),
            ).mappings()
        )
    names = {r["id"]: r["filename"] for r in rows}
    missing = [i for i in ids if i not in names]
    if missing:
        raise LookupError(f"documentos no disponibles (no existen o no están listos): {missing}")

    # Resumen por documento en paralelo (cada uno sirve su caché si existe).
    with ThreadPoolExecutor(max_workers=settings.summary_max_workers) as pool:
        summaries = list(pool.map(summarize_document, ids))

    items = [(names[i], s) for i, s in zip(ids, summaries)]

    logger.info("resumen multi-documento: %d clases → reduce final", len(ids))
    return llm.generate(build_multi_reduce_prompt(items))


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
