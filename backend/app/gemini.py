"""Cliente de la Gemini API (embeddings + generación).

- Por defecto: AI Studio (free tier) con API key.
- Vertex AI: poner GOOGLE_GENAI_USE_VERTEXAI=true — mismo SDK, autenticación
  por Workload Identity/ADC. Es la ruta de upgrade documentada.
"""

import json
import logging

from google import genai
from google.genai import types

from .config import settings

logger = logging.getLogger("rag.gemini")

_client = None


def get_client() -> genai.Client:
    global _client
    if _client is not None:
        return _client
    if settings.google_genai_use_vertexai.lower() == "true":
        _client = genai.Client(
            vertexai=True, project=settings.project_id, location=settings.region
        )
    else:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def embed_texts(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Vectoriza textos con el MISMO modelo y dimensión en ingesta y consulta."""
    res = get_client().models.embed_content(
        model=settings.embedding_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embedding_dims,
        ),
    )
    return [e.values for e in res.embeddings]


def embed_query(text: str) -> list[float]:
    """Vectoriza una consulta (task_type=RETRIEVAL_QUERY)."""
    return embed_texts([text], task_type="RETRIEVAL_QUERY")[0]


def generate(prompt: str, model: str | None = None) -> str:
    """Genera texto. `model` permite usar otro modelo distinto de GEN_MODEL
    (p. ej. el de conocimiento general, GENERAL_MODEL)."""
    res = get_client().models.generate_content(
        model=model or settings.gen_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2, max_output_tokens=settings.max_output_tokens
        ),
    )
    return res.text or ""


def vector_to_literal(vec: list[float]) -> str:
    """Convierte un embedding al literal textual que pgvector acepta: '[...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


_REWRITE_PROMPT = """Eres un optimizador de búsqueda semántica para un sistema RAG.
1. Reescribe la pregunta del usuario para que sea clara, autocontenida y eficaz
   para búsqueda vectorial (corrige ambigüedades, typos y lenguaje vago).
2. Genera {n} variantes parafraseadas de la misma (multi-query expansion).

Responde SOLO con un JSON válido, sin texto adicional ni fences:
{{"rewritten": "pregunta reescrita", "variants": ["variante 1", "variante 2"]}}

Pregunta del usuario: {question}"""


def rewrite_and_expand(question: str, n_variants: int) -> dict:
    """Query rewriting + query expansion en UNA llamada al LLM.

    Devuelve {"rewritten": str, "variants": list[str]}. Si la llamada o el
    parseo fallan, cae a la pregunta original (la consulta nunca se rompe
    por culpa de la optimización).
    """
    fallback = {"rewritten": question, "variants": []}
    try:
        raw = generate(_REWRITE_PROMPT.format(n=n_variants, question=question)).strip()
        if raw.startswith("```"):  # tolerar fences ```json ... ```
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        rewritten = str(data.get("rewritten") or question).strip() or question
        variants = [str(v).strip() for v in (data.get("variants") or []) if str(v).strip()]
        return {"rewritten": rewritten, "variants": variants[:n_variants]}
    except Exception as exc:
        logger.warning("rewrite_and_expand falló; usando la pregunta original: %s", exc)
        return fallback
