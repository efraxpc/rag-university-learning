-- Validación 3 (índice vectorial) del plan de la Fase 3.
-- Uso: cloud-sql-proxy ... &  →  psql "host=127.0.0.1 dbname=ragdb user=app" -f scripts/validate_db.sql

-- 3a. Extensión pgvector presente
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';

-- 3b. Esquema de chunks: columna embedding vector(EMBEDDING_DIMS, p. ej. 1536) + índices
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'chunks' ORDER BY ordinal_position;

SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'chunks';

-- 3c. Contenido tras la ingesta (modelo small-to-big)
SELECT
  (SELECT count(*) FROM documents) AS documentos,
  (SELECT count(*) FROM parents)   AS parents,
  (SELECT count(*) FROM chunks)    AS chunks,
  (SELECT count(*) FROM chunks WHERE embedding IS NOT NULL) AS con_vector,
  (SELECT count(*) FROM chunks WHERE parent_id IS NOT NULL) AS con_parent;

-- 3d. La búsqueda usa el índice HNSW (Index Scan, no Seq Scan).
-- Sustituir el literal por un embedding real si se quiere un plan exacto.
EXPLAIN
SELECT c.id
FROM chunks c
ORDER BY c.embedding <=> (SELECT embedding FROM chunks LIMIT 1)
LIMIT 4;
