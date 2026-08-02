-- Inicialización del esquema RAG en Cloud SQL PostgreSQL.
-- Uso: cloud-sql-proxy INSTANCE_CONNECTION_NAME &
--      psql "host=127.0.0.1 dbname=ragdb user=app password=***" -f scripts/init_db.sql
--
-- Modelo small-to-big: los embeddings viven en `chunks` (hijos, pequeños) y el
-- contexto devuelto al LLM vive en `parents` (chunks grandes). En modo simple
-- (SMALL_TO_BIG=false) los chunks tienen parent_id NULL.

CREATE EXTENSION IF NOT EXISTS vector;

-- Sesiones de chat: agrupan documentos (una clase por sesión, p. ej.).
-- Se crea ANTES de documents porque documents.session_id la referencia.
CREATE TABLE IF NOT EXISTS sessions (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS documents (
    id         BIGSERIAL PRIMARY KEY,
    filename   TEXT        NOT NULL,
    gcs_uri    TEXT        NOT NULL,
    -- Sesión a la que pertenece el documento (borrar la sesión borra en
    -- cascada sus documentos, chunks y parents).
    session_id BIGINT      NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
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

-- Migración idempotente para DBs creadas antes de las sesiones: la columna
-- queda nullable a nivel DB (el NOT NULL solo aplica en la definición fresca)
-- y la aplicación valida que todo documento nuevo lleva session_id.
ALTER TABLE documents ADD COLUMN IF NOT EXISTS session_id BIGINT
    REFERENCES sessions (id) ON DELETE CASCADE;

-- Índice B-tree para filtrar documentos por sesión (tras la migración, para
-- que también exista en DBs legacy donde la columna llega por el ALTER).
CREATE INDEX IF NOT EXISTS documents_session_id_idx ON documents (session_id);

-- Migración de datos: si hay documentos huérfanos (sin sesión) y no existe
-- ninguna sesión, se crea la sesión "General" y se les asigna. Si no hay
-- huérfanos no se crea nada: los despliegues nuevos crean sesiones desde la UI.
INSERT INTO sessions (name)
SELECT 'General'
WHERE NOT EXISTS (SELECT 1 FROM sessions)
  AND EXISTS (SELECT 1 FROM documents WHERE session_id IS NULL);

UPDATE documents
SET session_id = (SELECT id FROM sessions WHERE name = 'General' ORDER BY id LIMIT 1)
WHERE session_id IS NULL
  AND EXISTS (SELECT 1 FROM sessions WHERE name = 'General');
