"""Cliente de generación LLM con motor seleccionable: Anthropic o Gemini.

Patrón Strategy (de comportamiento, no de cliente): cada estrategia envuelve
su SDK tras una interfaz común `generate(prompt, model, max_tokens) -> str`,
y la factory `_build_strategy` instancia la elegida por `LLM_PROVIDER`:

- `anthropic` (DEFAULT): API directa de Anthropic — requiere
  `ANTHROPIC_API_KEY`.
- `vertex`: Anthropic Claude vía Vertex AI Model Garden — ADC en local
  (gcloud auth application-default login) / Workload Identity en GKE;
  requiere el modelo habilitado en Model Garden del proyecto y el rol
  roles/aiplatform.user sobre la identidad usada.
- `gemini`: Google Gemini — reutiliza el cliente de app/gemini.py: AI
  Studio con `GEMINI_API_KEY` o Vertex AI con
  `GOOGLE_GENAI_USE_VERTEXAI=true` (Workload Identity/ADC).

Así, `generate()`/`rewrite_and_expand()` y sus consumidores (rag.py,
routers/query.py) no conocen el motor activo. El .env solo elige el motor:
los modelos se resuelven en código por ROL (gen/general/fast) con el
catálogo `MODELS` de cada estrategia — es imposible configurar un motor con
modelos de otra familia.

Los embeddings NO pasan por aquí: siguen en app/gemini.py (Anthropic no
tiene modelo de embeddings).
"""

import json
import logging
import threading
from typing import Literal, Protocol

from anthropic import Anthropic, AnthropicVertex
from google import genai
from google.genai import types as genai_types

from . import gemini
from .config import settings

logger = logging.getLogger("rag.llm")

# Roles de modelo: la interfaz pública habla de ROLES, no de modelos
# concretos. Cada estrategia declara su propio catálogo MODELS (rol →
# modelo), así la coherencia motor↔modelos vive en el código y el .env solo
# elige el motor (LLM_PROVIDER).
Role = Literal["gen", "general", "fast"]


class _Strategy(Protocol):
    """Interfaz común de las estrategias de generación (patrón Strategy)."""

    MODELS: dict[Role, str]  # catálogo rol → modelo concreto del motor

    def generate(self, prompt: str, model: str, max_tokens: int) -> str: ...


class _AnthropicStrategy:
    """API directa de Anthropic o Vertex AI Model Garden (mismo SDK)."""

    MODELS: dict[Role, str] = {
        "gen": "claude-fable-5",      # respuestas finales y reduces
        "general": "claude-fable-5",  # conocimiento general
        "fast": "claude-haiku-4-5",   # volumen: rewrite/expand, maps, títulos
    }

    def __init__(self, client: Anthropic | AnthropicVertex) -> None:
        self._client = client

    def generate(self, prompt: str, model: str, max_tokens: int) -> str:
        res = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if res.stop_reason == "refusal":  # posible en Fable 5 (Covered Model)
            logger.warning("el modelo %r rechazó la generación (refusal)", res.model)
            return ""
        if res.stop_reason == "max_tokens":  # respuesta cortada a media frase
            logger.warning(
                "la generación con %r se TRUNCÓ por max_tokens (%s)", res.model, max_tokens
            )
        return "".join(b.text for b in res.content if b.type == "text")


class _GeminiStrategy:
    """Google Gemini (AI Studio o Vertex AI, vía app/gemini.py)."""

    MODELS: dict[Role, str] = {
        "gen": "gemini-3.1-pro-preview",      # respuestas finales y reduces
        "general": "gemini-3.1-pro-preview",  # conocimiento general
        "fast": "gemini-3.5-flash",   # volumen: rewrite/expand, maps, títulos
    }

    def __init__(self, client: genai.Client) -> None:
        self._client = client

    def generate(self, prompt: str, model: str, max_tokens: int) -> str:
        res = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(max_output_tokens=max_tokens),
        )
        feedback = getattr(res, "prompt_feedback", None)
        if feedback is not None and getattr(feedback, "block_reason", None):
            logger.warning(
                "Gemini bloqueó la generación (%s)", feedback.block_reason
            )
            return ""
        candidates = getattr(res, "candidates", None) or []
        if candidates and str(getattr(candidates[0], "finish_reason", "")).endswith("MAX_TOKENS"):
            logger.warning(
                "la generación con %r se TRUNCÓ por max_tokens (%s)", model, max_tokens
            )
        return res.text or ""


def _build_strategy() -> _Strategy:
    """Factory del patrón Strategy: construye la estrategia de generación
    elegida por `LLM_PROVIDER` (`anthropic` | `vertex` | `gemini`)."""
    provider = settings.llm_provider.strip().lower()
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic requiere ANTHROPIC_API_KEY "
                "(https://console.anthropic.com/) o cambia a LLM_PROVIDER=vertex"
            )
        return _AnthropicStrategy(Anthropic(api_key=settings.anthropic_api_key))
    if provider == "vertex":
        return _AnthropicStrategy(
            AnthropicVertex(
                region=settings.anthropic_vertex_region,
                project_id=settings.project_id,
            )
        )
    if provider == "gemini":
        return _GeminiStrategy(gemini.new_client())
    raise ValueError(
        f"LLM_PROVIDER={settings.llm_provider!r} no válido: usa 'anthropic', 'vertex' o 'gemini'"
    )


# Estrategia POR HILO: mismo patrón que app/gemini.py — los workers del map
# paralelo de resúmenes comparten proceso y el httpx interno de los SDK no
# es seguro compartiéndolo entre hilos.
_tls = threading.local()


def get_strategy() -> _Strategy:
    strategy = getattr(_tls, "strategy", None)
    if strategy is None:
        strategy = _build_strategy()
        _tls.strategy = strategy
    return strategy


def generate(prompt: str, role: Role = "gen", max_tokens: int | None = None) -> str:
    """Genera texto con el motor elegido por LLM_PROVIDER, usando el modelo
    del catálogo de la estrategia para el ROL pedido:
    - "gen" (default): respuestas finales del chat y reduces de resúmenes.
    - "general": complemento/respuesta de conocimiento general.
    - "fast": llamadas de volumen (query rewrite/expansion, map de resúmenes,
      títulos de clase).
    `max_tokens` permite otro presupuesto de salida (p. ej.
    SUMMARY_MAX_OUTPUT_TOKENS para los resúmenes finales)."""
    strategy = get_strategy()
    model = strategy.MODELS[role]
    logger.debug("generate rol=%s → modelo=%s", role, model)
    return strategy.generate(
        prompt,
        model,
        max_tokens or settings.max_output_tokens,
    )


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
    """Query rewriting + query expansion en UNA llamada al LLM (rol "fast").

    Devuelve {"rewritten": str, "variants": list[str]}. Si la llamada o el
    parseo fallan, cae a la pregunta original (la consulta nunca se rompe
    por culpa de la optimización).
    """
    fallback = {"rewritten": question, "variants": []}
    try:
        raw = generate(
            _REWRITE_PROMPT.format(n=n_variants, question=question),
            role="fast",
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
