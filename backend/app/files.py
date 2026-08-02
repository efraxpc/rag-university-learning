"""Borrado físico de ficheros de documentos (disco local o blob de GCS).

Lo comparten los routers de documentos (DELETE /documents/{id}) y de
sesiones (DELETE /sessions/{id}, que borra todos los ficheros de la sesión).
"""

import logging
from pathlib import Path

from .config import settings

logger = logging.getLogger("rag.files")


def delete_physical_file(uri: str, doc_id: int) -> None:
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
