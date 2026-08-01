-- Inicialización del esquema RAG en Cloud SQL PostgreSQL.
-- Uso: cloud-sql-proxy INSTANCE_CONNECTION_NAME &
--      psql "host=127.0.0.1 dbname=ragdb user=app password=***" -f scripts/init_db.sql
--
-- Modelo small-to-big: los embeddings viven en `chunks` (hijos, pequeños) y el
-- contexto devuelto al LLM vive en `parents` (chunks grandes). En modo simple
-- (SMALL_TO_BIG=false) los chunks tienen parent_id NULL.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id         BIGSERIAL PRIMARY KEY,
    filename   TEXT        NOT NULL,
    gcs_uri    TEXT        NOT NULL,
    status     TEXT        NOT NULL DEFAULT 'pending', -- pending | ready | error
    -- Caché del resumen de clase entera (map-reduce, ver backend/app/rag.py).
    -- NULL = aún no calculado; se rellena en la primera petición de resumen.
    summary    TEXT,
    -- Título de la clase auto-generado por el LLM (backend/app/rag.py).
    -- NULL = pendiente; se genera de forma perezosa al listar documentos
    -- ready (GET /documents) y se muestra en el frontend.
    title      TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Chunks grandes (contexto). No se vectorizan.
CREATE TABLE IF NOT EXISTS parents (
    id           BIGSERIAL PRIMARY KEY,
    document_id  BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    parent_index INTEGER NOT NULL,
    content      TEXT NOT NULL
);

-- Chunks pequeños vectorizados (búsqueda). parent_id NULL = modo simple.
CREATE TABLE IF NOT EXISTS chunks (
    id          BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    parent_id   BIGINT REFERENCES parents (id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content     TEXT NOT NULL,
    -- Dimensión de los embeddings (EMBEDDING_DIMS / output_dimensionality).
    -- pgvector limita los índices HNSW/IVFFlat a 2000 dims → 1536 recomendado.
    embedding   vector(1536) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Búsqueda ANN por coseno (baja latencia) sobre los chunks pequeños.
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- Índices B-tree para la búsqueda híbrida (filtros por metadatos).
CREATE INDEX IF NOT EXISTS chunks_document_id_idx ON chunks (document_id);
CREATE INDEX IF NOT EXISTS chunks_parent_id_idx ON chunks (parent_id);
CREATE INDEX IF NOT EXISTS parents_document_id_idx ON parents (document_id);
CREATE INDEX IF NOT EXISTS documents_created_at_idx ON documents (created_at DESC);

-- Migración idempotente para DBs creadas antes de las columnas summary y
-- title (CREATE TABLE IF NOT EXISTS no altera tablas ya existentes).
ALTER TABLE documents ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS title TEXT;
