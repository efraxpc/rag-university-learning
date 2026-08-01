# RAG University — RAG en GCP con GKE + Cloud SQL (pgvector) + Claude (Model Garden)

Búsqueda conversacional sobre tus documentos (PDF/TXT/MD): FastAPI + Next.js en
**GKE Autopilot**, vectores en **Cloud SQL PostgreSQL + pgvector** (HNSW),
embeddings con la **Gemini API** y generación con **Anthropic Claude Fable 5
vía Vertex AI Model Garden**. Coste objetivo: **~15–35 $/mes**.

La arquitectura completa (decisiones, diagrama, diseño y validación) está en
[`solution-architecture-guide.md`](solution-architecture-guide.md) y el diagrama
en [`diagram.mmd`](diagram.mmd).

## Estructura

```
backend/    API orquestadora FastAPI (ingesta + consulta RAG)
chunker/    Job K8s: extraer → trocear → vectorizar → insertar en pgvector
frontend/   UI Next.js (chat + subida de documentos)
terraform/  VPC+IP privada, GKE Autopilot, Cloud SQL, bucket, Secret Manager, IAM
k8s/        Manifiestos (placeholders ${...} → los sustituye scripts/deploy.sh)
scripts/    deploy.sh, session-start/end.sh (runbook de costes), validate.*
```

## Quickstart (despliegue en GCP)

**Prerequisitos**: `gcloud`, `terraform` ≥ 1.6, `kubectl`, `cloud-sql-proxy`,
`psql`; un proyecto GCP con facturación; **alerta de presupuesto** creada;
API key de Gemini (embeddings) en https://aistudio.google.com/apikey;
**Claude Fable 5 habilitado en Model Garden** del proyecto (consola de
Vertex AI) para la generación.

```bash
# 1. Variables
cp terraform/terraform.tfvars.example terraform/terraform.tfvars  # rellenar
export PROJECT_ID=tu-proyecto REGION=us-central1

# 2. Despliegue end-to-end (15-20 min la primera vez)
scripts/deploy.sh

# 3. Acceso (sin Load Balancer)
kubectl -n rag port-forward svc/web 3000:3000   # → http://localhost:3000
```

Uso: sube un PDF/TXT/MD/IPYNB, espera a que pase a `ready` y haz una pregunta.

## Runbook "mínimo disciplinado" (coste ≈ $0 fuera de sesiones)

```bash
scripts/session-end.sh     # escala a 0 + para Cloud SQL
scripts/session-start.sh   # activa Cloud SQL + escala a 1
terraform -chdir=terraform destroy   # teardown total
```

## Validación (post-despliegue)

```bash
export PROJECT_ID=... BUCKET=...
scripts/validate.sh        # checks 1-7 del plan de validación
psql "host=127.0.0.1 dbname=ragdb user=app" -f scripts/validate_db.sql
```

## Test local (sin tocar GCP)

Stack completo en local: pgvector en Docker + backend + chunker (subproceso) + frontend.

```bash
cp .env.example .env             # pega tu GEMINI_API_KEY (https://aistudio.google.com/apikey)
scripts/local-test.sh start      # levanta todo y muestra los LOGS EN VIVO
                                 # (Ctrl+C solo deja de verlos; los servicios siguen)
scripts/local-test.sh start -d   # igual pero sin logs en vivo (todo en background)
scripts/local-test.sh logs       # ver logs en vivo más tarde (api + web; o: logs api | logs web)
scripts/local-test.sh restart    # reinicia todo (también admite -d)
scripts/local-test.sh stop       # para todo (conserva los datos de la DB)
```

La configuración se lee con prioridad **variable de entorno → `.env` (raíz) →
default**: `GEMINI_API_KEY` (obligatoria), `EMBEDDING_MODEL`, `EMBEDDING_DIMS`
(default `1536`), `GEN_MODEL` y los parámetros de chunking (`SMALL_TO_BIG`,
`PARENT_CHUNK_SIZE/OVERLAP`, `SMALL_CHUNK_SIZE/OVERLAP`). `.env` está en
`.gitignore`. **El backend (`config.py`) y el chunker leen la `.env` raíz por
ruta absoluta**, así que también funcionan lanzados a mano (sin `local-test.sh`)
— solo necesitas `DATABASE_URL` exportada o descomentada en `.env`.

### Optimizaciones RAG implementadas

- **Ventana deslizante (pre-retrieval)**: chunking por tamaño + solape configurables.
- **Small-to-big** (default ON): se vectorizan chunks **pequeños** (256 chars,
  más precisión semántica) pero la búsqueda devuelve el **parent** (1024 chars,
  más contexto al LLM). Los embeddings viven en `chunks`, el contexto en
  `parents` (`chunks.parent_id`). Con `SMALL_TO_BIG=false` vuelve al modo clásico.
- **Query rewriting** (default ON): el LLM reescribe la pregunta antes de buscar
  (clara, autocontenida, sin typos ni vaguedad).
- **Query expansion multi-query** (default ON): el LLM genera N paráfrasis
  (`EXPANSION_VARIANTS=3`), se busca con todas y se fusionan los resultados
  por parent (mínima distancia). Rewriting + expansion se hacen en **una llamada**
  al LLM; la generación usa siempre la **pregunta original**. Debug en
  `api.log` (`query opt: original=... → consultas candidatas`).

**Dimensión de embeddings**: `EMBEDDING_DIMS` fija el `output_dimensionality`
(Matryoshka) y debe coincidir con `vector(N)` de `scripts/init_db.sql`.
pgvector limita los índices HNSW/IVFFlat a **2000 dims** → aunque
`gemini-embedding-001` soporte 3072, usa 1536 (recomendado). Si cambias el
valor, recrea las tablas: `DROP TABLE chunks, documents;` + `local-test.sh start`.

El script: (1) lanza `pgvector/pgvector:pg16` en `127.0.0.1:55432` (sin chocar
con un Postgres local en 5432), (2) aplica `init_db.sql`, (3) crea el venv,
(4) arranca el backend en `:8000` con `BUCKET_NAME=""` → **modo local**: los
documentos se guardan en `backend/local-docs/` y el chunker se ejecuta como
subproceso (el MISMO código que el Job de K8s), (5) arranca Next.js en `:3000`.

También puedes ejecutar el chunker a mano contra un fichero local:

```bash
cd chunker && DOCUMENT_ID=1 FILE_NAME=uploads/mi-doc.pdf MOUNT_PATH=/ruta/local \
  DATABASE_URL="postgresql+pg8000://app:app@127.0.0.1:55432/ragdb" \
  GEMINI_API_KEY="***" python main.py
```

Para desarrollo contra Cloud SQL real, usa `cloud-sql-proxy` y deja
`DATABASE_URL` apuntando al túnel (ver guía de arquitectura, sección 6.6).


## Migración a Vertex AI (producción)

Mismo código: poner `GOOGLE_GENAI_USE_VERTEXAI=true` en los Deployments/Jobs y
habilitar `aiplatform.googleapis.com`. La autenticación pasa a ser Workload
Identity (sin API key).
