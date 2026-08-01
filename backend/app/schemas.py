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


class SourceOut(BaseModel):
    document_id: int
    filename: str
    content: str
    distance: float


class QueryOut(BaseModel):
    answer: str
    sources: list[SourceOut]
