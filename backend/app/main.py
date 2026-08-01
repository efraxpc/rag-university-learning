"""API orquestadora del RAG (FastAPI).

Flujo A (ingesta):  POST /documents → GCS + metadata + Job de chunking
Flujo B (consulta): POST /query     → búsqueda híbrida → prompt aumentado → Gemini
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import check_db
from .routers import documents, query

# Los logs van a stderr → uvicorn los recoge en api.log (visibles con
# `local-test.sh logs`). Sin esto, los errores de los endpoints solo
# llegaban al cliente HTTP y nunca al log.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("rag")

app = FastAPI(title="RAG orchestrator", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(query.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db")
def health_db() -> dict:
    """Validación 2 del plan: conectividad GKE → Cloud SQL por IP privada."""
    return {"db": "ok" if check_db() else "error"}
