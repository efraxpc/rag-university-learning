"""Esquemas Pydantic de entrada/salida de la API."""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    doc_count: int = 0


class DocumentOut(BaseModel):
    id: int
    filename: str
    gcs_uri: str
    # Sesión a la que pertenece (NULL solo en documentos legacy migrados).
    session_id: int | None = None
    status: str  # pending | ready | error
    title: str | None = None  # título auto-generado de la clase (NULL = pendiente)
    created_at: datetime


class QueryIn(BaseModel):
    question: str
    document_id: int | None = None  # filtro opcional → búsqueda híbrida
    # Sesión de chat: acota la búsqueda y los resúmenes a sus documentos.
    session_id: int | None = None
    # Selección múltiple de documentos para el resumen de clase entera
    # (botón del chat con checkboxes, incluida la opción "todas").
    document_ids: list[int] | None = None
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
