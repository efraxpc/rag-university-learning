"""Cliente de la Gemini API: embeddings SIEMPRE + generación opcional.

La vectorización vive aquí (Anthropic no tiene modelo de embeddings). La
generación de texto vive en app/llm.py, que reutiliza `new_client()` cuando
`LLM_PROVIDER=gemini` (patrón Strategy).

- Por defecto: AI Studio (free tier) con API key.
- Vertex AI: poner GOOGLE_GENAI_USE_VERTEXAI=true — mismo SDK, autenticación
  por Workload Identity/ADC. Es la ruta de upgrade documentada.
"""

import logging
import threading

from google import genai
from google.genai import types

from .config import settings

logger = logging.getLogger("rag.gemini")

# Cliente POR HILO: el httpx interno del SDK no es seguro compartiéndolo
# entre hilos (los embeddings se piden también desde el hilo principal y el
# GC de una instancia puede cerrar el httpx de otra, ver
# https://github.com/googleapis/python-genai/issues/1763).
_tls = threading.local()


def new_client() -> genai.Client:
    """Construye un cliente google-genai (AI Studio o Vertex AI). También la
    usa app/llm.py para la estrategia de generación Gemini."""
    if settings.google_genai_use_vertexai.lower() == "true":
        return genai.Client(
            vertexai=True, project=settings.project_id, location=settings.region
        )
    return genai.Client(api_key=settings.gemini_api_key)


def get_client() -> genai.Client:
    client = getattr(_tls, "client", None)
    if client is None:
        client = new_client()
        _tls.client = client
    return client


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


def vector_to_literal(vec: list[float]) -> str:
    """Convierte un embedding al literal textual que pgvector acepta: '[...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
