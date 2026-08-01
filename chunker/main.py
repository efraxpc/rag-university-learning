"""Job de chunking: extraer → limpiar → trocear → vectorizar → insertar.

Se ejecuta como K8s Job efímero (pod Spot) por cada documento subido:
- Lee el documento del bucket montado con GCS FUSE en /documents.
- Extrae el texto (pypdf para PDF; JSON cells para IPYNB; limpieza de cues y
  timestamps para VTT; lectura directa para TXT/MD).
- Troceo con ventana deslizante (tamaño + solape configurables).
- SMALL_TO_BIG=true: trocea en parents (grandes, contexto) y en children
  (pequeños); vectoriza SOLO los children y los vincula a su parent.
  SMALL_TO_BIG=false: vectoriza los parents directamente (modo clásico).
- Inserta en Cloud SQL y marca el documento 'ready'.

Patrón equivalente al embedding-job.py del tutorial oficial de Google Cloud.
"""

import os
import sys
from pathlib import Path

import sqlalchemy
from sqlalchemy import text


def _load_root_env() -> None:
    """Carga la .env de la raíz del repo como fallback (sin dependencias).
    Las variables ya exportadas en el entorno tienen prioridad."""
    env_file = Path(__file__).resolve().parents[1] / ".env"  # chunker/ → raíz
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


_load_root_env()

DOCUMENT_ID = int(os.environ["DOCUMENT_ID"])
BUCKET_NAME = os.environ.get("BUCKET_NAME", "")
FILE_NAME = os.environ.get("FILE_NAME", "")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-005")
# Dimensión de salida (Matryoshka); debe coincidir con vector(N) del esquema.
# pgvector limita los índices HNSW a 2000 dims → 1536 recomendado.
EMBEDDING_DIMS = int(os.environ.get("EMBEDDING_DIMS", "1536"))
# Small-to-big (optimización pre-retrieval) + ventana deslizante.
SMALL_TO_BIG = os.environ.get("SMALL_TO_BIG", "true").lower() == "true"
PARENT_CHUNK_SIZE = int(os.environ.get("PARENT_CHUNK_SIZE", "1024"))
PARENT_CHUNK_OVERLAP = int(os.environ.get("PARENT_CHUNK_OVERLAP", "128"))
SMALL_CHUNK_SIZE = int(os.environ.get("SMALL_CHUNK_SIZE", "256"))
SMALL_CHUNK_OVERLAP = int(os.environ.get("SMALL_CHUNK_OVERLAP", "50"))
EMBED_BATCH = 100
# En GKE el bucket se monta en /documents (GCS FUSE). En local se puede
# apuntar a cualquier directorio: MOUNT_PATH=/tmp/docs python main.py
MOUNT_PATH = os.environ.get("MOUNT_PATH", "/documents")


def get_engine():
    """Conector de Cloud SQL (IP privada) o DATABASE_URL directa en local."""
    instance = os.environ.get("INSTANCE_CONNECTION_NAME", "")
    if instance:
        from google.cloud.sql.connector import Connector, IPTypes

        connector = Connector()

        def getconn():
            return connector.connect(
                instance,
                "pg8000",
                user=os.environ.get("DB_USER", "app"),
                password=os.environ["DB_PASS"],
                db=os.environ.get("DB_NAME", "ragdb"),
                ip_type=IPTypes.PRIVATE,
            )

        return sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn)
    url = os.environ.get(
        "DATABASE_URL", "postgresql+pg8000://app:app@127.0.0.1:5432/ragdb"
    )
    return sqlalchemy.create_engine(url)


def get_genai_client():
    from google import genai

    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "false").lower() == "true":
        return genai.Client(
            vertexai=True,
            project=os.environ.get("PROJECT_ID", ""),
            location=os.environ.get("REGION", "us-central1"),
        )
    return genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def _extract_vtt(path: str) -> str:
    """Extrae el texto hablado de un WebVTT: quita la cabecera, los bloques
    NOTE/STYLE, los identificadores de cue, las líneas de timestamps y los
    tags inline; deduplica líneas consecutivas (muy común en subtítulos)."""
    import re

    ts_line = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+")
    inline_tag = re.compile(r"<[^>]+>")
    lines = []
    skip_block = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                skip_block = False  # fin de bloque (cues y NOTE/STYLE)
                continue
            if line.startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
                skip_block = line.startswith(("NOTE", "STYLE", "REGION"))
                continue
            if skip_block or ts_line.match(line) or line.isdigit():
                continue
            text_ = inline_tag.sub("", line).strip()
            # Dedupe de líneas consecutivas repetidas (karaoke de subtítulos).
            if text_ and (not lines or lines[-1] != text_):
                lines.append(text_)
    return "\n".join(lines)


def extract_text(path: str) -> str:
    if path.lower().endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if path.lower().endswith(".ipynb"):
        import json

        with open(path, encoding="utf-8") as fh:
            notebook = json.load(fh)
        parts = []
        for cell in notebook.get("cells", []):
            source = cell.get("source", [])
            text_ = "".join(source) if isinstance(source, list) else str(source)
            if not text_.strip():
                continue
            if cell.get("cell_type") == "code":
                parts.append(f"```python\n{text_}\n```")
            else:  # markdown / raw
                parts.append(text_)
        return "\n\n".join(parts)
    if path.lower().endswith(".vtt"):
        return _extract_vtt(path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def clean(text_: str) -> str:
    # Eliminar NULs (rompen Postgres) y colapsar espacios en blanco.
    return " ".join(text_.replace("\x00", " ").split())


def chunk_text(text_: str, size: int, overlap: int) -> list[str]:
    """Ventana deslizante por caracteres con solape (pre-retrieval sliding window)."""
    chunks = []
    start = 0
    while start < len(text_):
        end = start + size
        chunks.append(text_[start:end])
        start = end - overlap
    return [c for c in chunks if c.strip()]


def set_status(engine, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE documents SET status=:s WHERE id=:i"),
            {"s": status, "i": DOCUMENT_ID},
        )


def main() -> None:
    engine = get_engine()
    path = os.path.join(MOUNT_PATH, FILE_NAME)
    print(f"[chunker] doc={DOCUMENT_ID} file={path}", flush=True)

    try:
        raw = clean(extract_text(path))
        # 1. Parents: ventana deslizante grande (contexto para el LLM).
        parents = chunk_text(raw, PARENT_CHUNK_SIZE, PARENT_CHUNK_OVERLAP)
        if not parents:
            raise RuntimeError("El documento no contiene texto extraíble.")

        # 2. Unidades a vectorizar: children (small-to-big) o los propios parents.
        if SMALL_TO_BIG:
            units = [  # (parent_index, chunk_index, content)
                (pi, ci, child)
                for pi, parent in enumerate(parents)
                for ci, child in enumerate(
                    chunk_text(parent, SMALL_CHUNK_SIZE, SMALL_CHUNK_OVERLAP)
                )
            ]
        else:
            units = [(pi, 0, parent) for pi, parent in enumerate(parents)]
        mode = f"small-to-big ({len(parents)} parents × children de {SMALL_CHUNK_SIZE})"
        print(f"[chunker] {len(units)} chunks a vectorizar [{mode}]...", flush=True)

        # 3. Vectorizar por lotes (mismo modelo/dims que la consulta).
        client = get_genai_client()
        from google.genai import types

        embed_cfg = types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT", output_dimensionality=EMBEDDING_DIMS
        )
        contents = [u[2] for u in units]
        vectors: list[list[float]] = []
        for i in range(0, len(contents), EMBED_BATCH):
            res = client.models.embed_content(
                model=EMBEDDING_MODEL,
                contents=contents[i : i + EMBED_BATCH],
                config=embed_cfg,
            )
            vectors.extend(e.values for e in res.embeddings)

        # 4. Insertar parents y chunks en una sola transacción.
        with engine.begin() as conn:
            parent_ids = {}
            for pi, parent in enumerate(parents):
                parent_ids[pi] = conn.execute(
                    text(
                        "INSERT INTO parents (document_id, parent_index, content) "
                        "VALUES (:doc, :idx, :content) RETURNING id"
                    ),
                    {"doc": DOCUMENT_ID, "idx": pi, "content": parent},
                ).scalar_one()
            rows = [
                {
                    "doc": DOCUMENT_ID,
                    "parent": parent_ids[pi] if SMALL_TO_BIG else None,
                    "idx": ci,
                    "content": content,
                    "emb": "[" + ",".join(f"{x:.6f}" for x in vec) + "]",
                }
                for (pi, ci, content), vec in zip(units, vectors)
            ]
            conn.execute(
                text(
                    "INSERT INTO chunks (document_id, parent_id, chunk_index, content, embedding) "
                    "VALUES (:doc, :parent, :idx, :content, CAST(:emb AS vector))"
                ),
                rows,
            )
        set_status(engine, "ready")
        print(f"[chunker] OK: {len(rows)} vectores insertados [{mode}].", flush=True)
    except Exception as exc:
        print(f"[chunker] ERROR: {exc}", flush=True)
        try:
            set_status(engine, "error")
        finally:
            sys.exit(1)


if __name__ == "__main__":
    main()
