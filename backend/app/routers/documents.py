"""Endpoints de ingesta y listado de documentos."""

import logging
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy import text

from ..config import settings
from ..db import get_engine
from ..schemas import DocumentOut

logger = logging.getLogger("rag.documents")

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".txt", ".md", ".ipynb", ".vtt"}

# backend/app/routers/documents.py → raíz del repo (donde vive chunker/)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _insert_document(filename: str, uri: str) -> int:
    try:
        with get_engine().begin() as conn:
            return conn.execute(
                text(
                    "INSERT INTO documents (filename, gcs_uri, status) "
                    "VALUES (:f, :u, 'pending') RETURNING id"
                ),
                {"f": filename, "u": uri},
            ).scalar_one()
    except Exception as exc:
        logger.exception("Error registrando el documento %s", filename)
        raise HTTPException(502, f"Error registrando el documento: {exc}") from exc


def _set_status(doc_id: int, status: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE documents SET status=:s WHERE id=:i"), {"s": status, "i": doc_id})


def _run_chunker_local(doc_id: int, object_name: str, docs_dir: Path) -> str:
    """Modo local: ejecuta el MISMO código del Job (chunker/main.py) como
    subproceso en background contra la base de datos local."""
    chunker = REPO_ROOT / "chunker" / "main.py"
    if not chunker.exists():
        raise RuntimeError(f"No se encuentra {chunker}")
    env = {
        **os.environ,
        "DOCUMENT_ID": str(doc_id),
        "FILE_NAME": object_name,
        "MOUNT_PATH": str(docs_dir),
        "EMBEDDING_MODEL": settings.embedding_model,
        "EMBEDDING_DIMS": str(settings.embedding_dims),
        "SMALL_TO_BIG": str(settings.small_to_big).lower(),
        "PARENT_CHUNK_SIZE": str(settings.parent_chunk_size),
        "PARENT_CHUNK_OVERLAP": str(settings.parent_chunk_overlap),
        "SMALL_CHUNK_SIZE": str(settings.small_chunk_size),
        "SMALL_CHUNK_OVERLAP": str(settings.small_chunk_overlap),
        "GOOGLE_GENAI_USE_VERTEXAI": settings.google_genai_use_vertexai,
        "GEMINI_API_KEY": settings.gemini_api_key,
    }
    # La salida del chunker (errores incluidos) va a .local-test/chunker.log,
    # que local-test.sh muestra en vivo junto a api.log y web.log.
    log_dir = REPO_ROOT / ".local-test"
    log_dir.mkdir(exist_ok=True)
    log_file = open(log_dir / "chunker.log", "a", encoding="utf-8")
    logger.info("Lanzando chunker local para document_id=%s (log: %s)", doc_id, log_file.name)
    subprocess.Popen(  # noqa: S603 — proceso hijo efímero, equivalente al Job de K8s
        [sys.executable, str(chunker)],
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return "local-subprocess"


@router.post("", status_code=202)
def upload_document(file: UploadFile) -> dict:
    """Flujo A (ingesta): doc crudo → metadata pending → chunking.

    - Con BUCKET_NAME (GCP): sube a Cloud Storage y crea el Job de K8s.
    - Sin BUCKET_NAME (local): guarda en disco y lanza el chunker como proceso.
    """
    ext = "." + (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Extensión no soportada ({ext}). Usa: {sorted(ALLOWED_EXTENSIONS)}")

    object_name = f"uploads/{uuid.uuid4().hex}-{file.filename}"

    if settings.bucket_name:
        from google.cloud import storage

        try:
            bucket = storage.Client(project=settings.project_id or None).bucket(settings.bucket_name)
            bucket.blob(object_name).upload_from_file(file.file, content_type=file.content_type)
        except Exception as exc:
            logger.exception("Error subiendo %s a Cloud Storage", file.filename)
            raise HTTPException(502, f"Error subiendo a Cloud Storage: {exc}") from exc
        uri = f"gs://{settings.bucket_name}/{object_name}"
        doc_id = _insert_document(file.filename or object_name, uri)
        try:
            from ..k8s_jobs import create_chunker_job

            job_name = create_chunker_job(doc_id, object_name)
        except Exception as exc:
            _set_status(doc_id, "error")
            logger.exception("El Job de chunking falló para document_id=%s", doc_id)
            raise HTTPException(502, f"Documento registrado pero el Job falló: {exc}") from exc
        return {"document_id": doc_id, "job": job_name, "gcs_uri": uri}

    # ---- Modo local (sin GCP) ----
    docs_dir = Path(settings.local_docs_dir).resolve()
    dest = docs_dir / object_name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as exc:
        logger.exception("Error guardando el fichero local %s", file.filename)
        raise HTTPException(500, f"Error guardando el fichero local: {exc}") from exc
    doc_id = _insert_document(file.filename or object_name, f"local://{dest}")
    try:
        job_name = _run_chunker_local(doc_id, object_name, docs_dir)
    except Exception as exc:
        _set_status(doc_id, "error")
        logger.exception("El chunker local falló para document_id=%s", doc_id)
        raise HTTPException(502, f"Documento registrado pero el chunker local falló: {exc}") from exc
    return {"document_id": doc_id, "job": job_name, "uri": f"local://{dest}"}


@router.get("")
def list_documents() -> list[DocumentOut]:
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, filename, gcs_uri, status, created_at "
                    "FROM documents ORDER BY created_at DESC LIMIT 100"
                )
            ).mappings()
            return [DocumentOut(**row) for row in rows]
    except Exception as exc:
        logger.exception("Error consultando la base de datos")
        raise HTTPException(502, f"Error consultando la base de datos: {exc}") from exc


def _delete_physical_file(uri: str, doc_id: int) -> None:
    """Borrado físico best-effort: si falla, la DB ya es consistente y solo
    queda un fichero/blob huérfano (se loguea warning, no rompe el 204)."""
    try:
        if uri.startswith("local://"):
            Path(uri[len("local://"):]).unlink(missing_ok=True)
        elif uri.startswith("gs://"):
            from google.cloud import storage

            bucket_name, _, object_name = uri[len("gs://"):].partition("/")
            storage.Client(project=settings.project_id or None).bucket(
                bucket_name
            ).blob(object_name).delete()
    except Exception:
        logger.warning(
            "No se pudo borrar el fichero físico %s (document_id=%s); queda huérfano",
            uri, doc_id, exc_info=True,
        )


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: int) -> None:
    """Borra un documento, sus chunks y parents (ON DELETE CASCADE) y el
    fichero físico (disco local o blob de GCS).

    Se permite borrar en cualquier estado: si estaba 'pending', el chunker en
    curso fallará por la FK y terminará con error en el log (Job efímero).
    """
    try:
        with get_engine().begin() as conn:
            uri = conn.execute(
                text("SELECT gcs_uri FROM documents WHERE id=:i"), {"i": doc_id}
            ).scalar()
            if uri is None:
                raise HTTPException(404, f"Documento {doc_id} no encontrado.")
            conn.execute(text("DELETE FROM documents WHERE id=:i"), {"i": doc_id})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error borrando el documento %s", doc_id)
        raise HTTPException(502, f"Error borrando el documento: {exc}") from exc

    _delete_physical_file(uri, doc_id)
    logger.info("Documento %s borrado (uri=%s)", doc_id, uri)
