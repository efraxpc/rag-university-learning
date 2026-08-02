"""Endpoints de sesiones de chat: agrupan documentos y acotan consultas.

Borrar una sesión borra en cascada (ON DELETE CASCADE) todos sus documentos,
chunks y parents; los ficheros físicos se borran después best-effort.
No se permite borrar la única sesión existente.
"""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from ..db import get_engine
from ..files import delete_physical_file
from ..schemas import SessionIn, SessionOut

logger = logging.getLogger("rag.sessions")

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", status_code=201)
def create_session(body: SessionIn) -> SessionOut:
    name = body.name.strip()
    try:
        with get_engine().begin() as conn:
            row = conn.execute(
                text("INSERT INTO sessions (name) VALUES (:n) RETURNING id, name, created_at"),
                {"n": name},
            ).mappings().one()
    except Exception as exc:
        logger.exception("Error creando la sesión %s", name)
        raise HTTPException(502, f"Error creando la sesión: {exc}") from exc
    return SessionOut(**row, doc_count=0)


@router.get("")
def list_sessions() -> list[SessionOut]:
    try:
        with get_engine().connect() as conn:
            rows = list(conn.execute(
                text(
                    "SELECT s.id, s.name, s.created_at,"
                    " COUNT(d.id) AS doc_count"
                    " FROM sessions s"
                    " LEFT JOIN documents d ON d.session_id = s.id"
                    " GROUP BY s.id"
                    " ORDER BY s.created_at DESC"
                )
            ).mappings())
    except Exception as exc:
        logger.exception("Error consultando la base de datos")
        raise HTTPException(502, f"Error consultando la base de datos: {exc}") from exc
    return [SessionOut(**row) for row in rows]


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: int) -> None:
    """Borra la sesión y, en cascada, todos sus documentos/chunks/parents;
    tras el commit borra los ficheros físicos (best-effort).

    No se permite borrar la última sesión: la UI exige subir documentos a una
    sesión y sin ninguna no se podría usar la aplicación.
    """
    try:
        with get_engine().begin() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM sessions WHERE id=:s"), {"s": session_id}
            ).scalar()
            if exists is None:
                raise HTTPException(404, f"Sesión {session_id} no encontrada.")
            total = conn.execute(text("SELECT COUNT(*) FROM sessions")).scalar_one()
            if total <= 1:
                raise HTTPException(400, "No se puede borrar la única sesión existente.")
            docs = list(conn.execute(
                text("SELECT id, gcs_uri FROM documents WHERE session_id=:s"),
                {"s": session_id},
            ).mappings())
            conn.execute(text("DELETE FROM sessions WHERE id=:s"), {"s": session_id})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error borrando la sesión %s", session_id)
        raise HTTPException(502, f"Error borrando la sesión: {exc}") from exc

    for doc in docs:
        delete_physical_file(doc["gcs_uri"], doc["id"])
    logger.info(
        "Sesión %s borrada (%d documentos en cascada)", session_id, len(docs)
    )
