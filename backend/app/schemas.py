"""Esquemas Pydantic de entrada/salida de la API."""

from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    status: str  # pending | ready | error
    created_at: datetime


class QueryIn(BaseModel):
    question: str
    document_id: int | None = None  # filtro opcional → búsqueda híbrida
    # True = resumen de la clase entera por metadatos (botón del chat).
    # También se activa por keywords en la pregunta (rag.is_summary_request).
    summarize: bool = False


class SourceOut(BaseModel):
    document_id: int
    filename: str
    content: str
    distance: float


class QueryOut(BaseModel):
    answer: str
    sources: list[SourceOut]
