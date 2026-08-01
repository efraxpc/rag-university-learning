"""Endpoint de consulta RAG (flujo B, tiempo real).

Estrategia de respuesta:
- Si los documentos responden a la pregunta (distancia <= MAX_DISTANCE):
  respuesta basada en el contenido de la clase + complemento de conocimiento
  general con GENERAL_MODEL.
- Si no: respuesta SOLO de conocimiento general (GENERAL_MODEL), sin fuentes.
"""

import logging

from fastapi import APIRouter, HTTPException

from .. import gemini, rag
from ..config import settings
from ..schemas import QueryIn, QueryOut

logger = logging.getLogger("rag.query")

router = APIRouter(tags=["query"])


def _generate_general(prompt: str) -> str:
    """Genera con GENERAL_MODEL; si no está disponible, cae a GEN_MODEL."""
    try:
        return gemini.generate(prompt, model=settings.general_model)
    except Exception:
        logger.warning(
            "GENERAL_MODEL %r no disponible; usando GEN_MODEL %r",
            settings.general_model, settings.gen_model,
        )
        return gemini.generate(prompt)


@router.post("/query")
def query(body: QueryIn) -> QueryOut:
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "La pregunta no puede estar vacía.")

    try:
        sources = rag.hybrid_search(question, body.document_id)
    except Exception as exc:
        logger.exception("Error en la búsqueda vectorial")
        raise HTTPException(502, f"Error en la búsqueda vectorial: {exc}") from exc

    try:
        if rag.has_answer(sources):
            # 1) Respuesta basada en el contenido de la clase (RAG).
            answer = rag.sanity_check(gemini.generate(rag.build_prompt(question, sources)), sources)
            # 2) Complemento de conocimiento general. Si falla, se devuelve
            #    solo la respuesta RAG (nunca se pierde la respuesta).
            try:
                complement = _generate_general(rag.build_complement_prompt(question, answer)).strip()
                if complement:
                    answer += (
                        "\n\n---\n\n## Complemento de Gemini (conocimiento general)\n\n"
                        + complement
                    )
            except Exception:
                logger.warning("No se pudo generar el complemento general", exc_info=True)
            return QueryOut(answer=answer, sources=sources)

        # La respuesta NO está en el RAG → solo conocimiento general.
        answer = _generate_general(rag.build_general_prompt(question))
        return QueryOut(answer=rag.sanity_check(answer, []), sources=[])
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error llamando a la Gemini API")
        raise HTTPException(502, f"Error llamando a la Gemini API: {exc}") from exc
