"""Cliente de generación LLM: Anthropic Claude con doble proveedor.

Patrón Strategy: dos estrategias intercambiables de conexión, seleccionadas
en runtime por `LLM_PROVIDER` (factory `_build_client`):

- `anthropic` (DEFAULT): API directa de Anthropic — requiere
  `ANTHROPIC_API_KEY`.
- `vertex`: Vertex AI Model Garden — autenticación por ADC en local
  (gcloud auth application-default login) / Workload Identity en GKE;
  requiere el modelo habilitado en Model Garden del proyecto y el rol
  roles/aiplatform.user sobre la identidad usada.

Ambos clientes del SDK `anthropic` exponen la misma interfaz
(`messages.create`), así que `generate()`/`rewrite_and_expand()` y sus
consumidores (rag.py, routers/query.py) no conocen el proveedor activo.

- Modelos: GEN_MODEL (claude-fable-5) para las respuestas finales,
  GENERAL_MODEL para el conocimiento general y FAST_MODEL
  (claude-haiku-4-5) para llamadas auxiliares de volumen (query
  rewrite/expansion y el map paralelo de resúmenes). Los IDs pueden variar
  según el proveedor (Vertex admite sufijos `@fecha`): se ajustan por
  variables de entorno.

Los embeddings NO pasan por aquí: siguen en app/gemini.py (Anthropic no
tiene modelo de embeddings).
"""

import json
import logging
import threading

from anthropic import Anthropic, AnthropicVertex

from .config import settings

logger = logging.getLogger("rag.llm")

# Cliente POR HILO: mismo patrón que app/gemini.py — los workers del map
# paralelo de resúmenes comparten proceso y el httpx interno del SDK no es
# seguro compartiéndolo entre hilos.
_tls = threading.local()


def _build_client() -> Anthropic | AnthropicVertex:
    """Factory del patrón Strategy: construye la estrategia de conexión
    elegida por `LLM_PROVIDER` (`anthropic` | `vertex`)."""
    provider = settings.llm_provider.strip().lower()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic requiere ANTHROPIC_API_KEY "
                "(https://console.anthropic.com/) o cambia a LLM_PROVIDER=vertex"
            )
        return Anthropic(api_key=settings.anthropic_api_key)
    if provider == "vertex":
        return AnthropicVertex(
            region=settings.anthropic_vertex_region,
            project_id=settings.project_id,
        )
    raise ValueError(
        f"LLM_PROVIDER={settings.llm_provider!r} no válido: usa 'anthropic' o 'vertex'"
    )


def get_client() -> Anthropic | AnthropicVertex:
    client = getattr(_tls, "client", None)
    if client is None:
        client = _build_client()
        _tls.client = client
    return client


def generate(prompt: str, model: str | None = None, max_tokens: int | None = None) -> str:
    """Genera texto con la Messages API de Anthropic. `model` permite usar
    otro modelo distinto de GEN_MODEL (p. ej. FAST_MODEL para llamadas
    auxiliares o GENERAL_MODEL para el conocimiento general) y `max_tokens`
    otro presupuesto de salida (p. ej. SUMMARY_MAX_OUTPUT_TOKENS para los
    resúmenes finales, más largos que una respuesta de chat)."""
    res = get_client().messages.create(
        model=model or settings.gen_model,
        max_tokens=max_tokens or settings.max_output_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    if res.stop_reason == "refusal":  # posible en Fable 5 (Covered Model)
        logger.warning("el modelo %r rechazó la generación (refusal)", res.model)
        return ""
    if res.stop_reason == "max_tokens":  # respuesta cortada a media frase
        logger.warning(
            "la generación con %r se TRUNCÓ por max_tokens (%s)",
            res.model, max_tokens or settings.max_output_tokens,
        )
    return "".join(b.text for b in res.content if b.type == "text")


_REWRITE_PROMPT = """You are a semantic search optimizer for a RAG system.
1. Rewrite the user's question so it is clear, self-contained and effective
   for vector search (fix ambiguities, typos and vague wording).
2. Generate {n} paraphrased variants of it (multi-query expansion).

Keep the rewritten question and the variants in SPANISH (the indexed
documents are in Spanish).

Reply ONLY with valid JSON, no extra text and no fences:
{{"rewritten": "rewritten question", "variants": ["variant 1", "variant 2"]}}

User question: {question}"""


def rewrite_and_expand(question: str, n_variants: int) -> dict:
    """Query rewriting + query expansion en UNA llamada al LLM (FAST_MODEL).

    Devuelve {"rewritten": str, "variants": list[str]}. Si la llamada o el
    parseo fallan, cae a la pregunta original (la consulta nunca se rompe
    por culpa de la optimización).
    """
    fallback = {"rewritten": question, "variants": []}
    try:
        raw = generate(
            _REWRITE_PROMPT.format(n=n_variants, question=question),
            model=settings.fast_model,
        ).strip()
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
