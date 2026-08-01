"""Endpoint de consulta RAG (flujo B, tiempo real)."""

import logging

from fastapi import APIRouter, HTTPException

from .. import gemini, rag
from ..schemas import QueryIn, QueryOut

logger = logging.getLogger("rag.query")

router = APIRouter(tags=["query"])


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

    if not sources:
        return QueryOut(
            answer="No hay documentos indexados que respondan a tu pregunta. "
            "Sube un documento primero y espera a que esté 'ready'.",
            sources=[],
        )

    prompt = rag.build_prompt(question, sources)
    try:
        answer = gemini.generate(prompt)
    except Exception as exc:
        logger.exception("Error llamando a la Gemini API")
        raise HTTPException(502, f"Error llamando a la Gemini API: {exc}") from exc

    return QueryOut(answer=rag.sanity_check(answer, sources), sources=sources)
