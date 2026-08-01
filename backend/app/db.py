"""Conexión a Cloud SQL PostgreSQL.

- En GKE: Cloud SQL Python Connector (pg8000) contra la IP privada de la
  instancia, con autenticación IAM (Workload Identity). Recomendación oficial:
  https://docs.cloud.google.com/sql/docs/postgres/language-connectors
- En local: DATABASE_URL directa (p. ej. a través de cloud-sql-proxy).
"""

import sqlalchemy
from sqlalchemy import text

from .config import settings

_engine = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine

    if settings.instance_connection_name:
        from google.cloud.sql.connector import Connector, IPTypes

        connector = Connector()

        def getconn():
            return connector.connect(
                settings.instance_connection_name,
                "pg8000",
                user=settings.db_user,
                password=settings.db_pass,
                db=settings.db_name,
                ip_type=IPTypes.PRIVATE,
            )

        _engine = sqlalchemy.create_engine(
            "postgresql+pg8000://", creator=getconn, pool_size=2, max_overflow=2
        )
    else:
        url = settings.database_url or "postgresql+pg8000://app:app@127.0.0.1:5432/ragdb"
        _engine = sqlalchemy.create_engine(url, pool_size=2, max_overflow=2)

    return _engine


def check_db() -> bool:
    """SELECT 1 — usado por /health/db en las validaciones."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
