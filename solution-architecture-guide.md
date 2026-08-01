# Solución Google Cloud: RAG para búsqueda empresarial con GKE y SQL (pgvector)

> Generada con la skill `google-cloud-solution-rag-enterprise-search-gke-sqldb`
> (Fases 1–4 completadas y aprobadas por el usuario). Variantes aprobadas sobre
> la referencia: **Cloud SQL + pgvector** en lugar de AlloyDB, y **Gemini API
> gestionada** (embeddings + generación) en lugar de Gemma/vLLM self-hosted.

## 1. Resumen ejecutivo y visión general

Sistema RAG de búsqueda conversacional sobre documentos empresariales privados
(PDF/TXT/MD). El usuario sube documentos manualmente y hace preguntas en
lenguaje natural desde una UI web; el sistema responde con citas a las fuentes.
Todos los componentes de aplicación (UI Next.js, API orquestadora FastAPI, job
de chunking) se alojan en contenedores en un clúster **GKE Autopilot**; los
embeddings y la generación usan la **Gemini API** gestionada; los vectores y
metadatos viven en **Cloud SQL para PostgreSQL con pgvector** (índice HNSW).
Coste objetivo: **~15–35 USD/mes** con runbook de escala a cero.

## 2. Requisitos y estado actual

### 2.1. Requisitos funcionales

- **Datos**: documentos no estructurados (PDF, TXT, MD, IPYNB).
- **Ingesta**: manual / bajo demanda (el usuario sube archivos desde la UI).
- **Consulta**: Q&A de **un turno** + **búsqueda híbrida** (vectorial + filtros
  por metadatos, p. ej. `document_id`).

### 2.2. Requisitos no funcionales

- **Seguridad**: básica — IAM, IP privada para la DB, secretos en Secret Manager.
- **Fiabilidad**: sin HA estricta (aprendizaje); backups diarios de Cloud SQL.
- **Coste**: **mínimo disciplinado** (db-f1-micro, sin Load Balancer, Spot pods,
  escala a cero fuera de sesiones, alerta de presupuesto).
- **Operaciones**: estándar — Cloud Logging/Monitoring + Query Insights.
- **Rendimiento**: interactivo, < ~3 s extremo a extremo.
- **Sostenibilidad**: sin requisito.

### 2.3. Estado actual

Proyecto nuevo en Google Cloud; sin solución previa ni migración.

### 2.4. Dependencias

Ninguna externa. (API key de AI Studio como único artefacto fuera del proyecto.)

## 3. Descomposición técnica del workload

| # | Componente | Descripción |
|---|---|---|
| 1 | Ingesta de datos | Almacén de blobs para documentos crudos; la API registra metadata (pending). |
| 2 | Procesamiento y chunking | Pipeline containerizado por documento: extraer → limpiar → trocear con **ventana deslizante** (tamaño/solape configurables). **Small-to-big** (default): parents de 1024 chars (contexto) × children de 256 chars (vectores). |
| 3 | Generación de embeddings | **Gemini API (embeddings)** gestionada; mismo modelo en ingesta y consulta (text-embedding-005). |
| 4 | Almacén e índice vectorial | **SQL con pgvector**: tabla `chunks` con `vector(1536)` (Matryoshka, `output_dimensionality`) + índice **HNSW** (coseno). Nota: pgvector limita los índices ANN a 2000 dims → 1536 aunque el modelo soporte 3072. |
| 5 | Datos no vectoriales | Tablas `documents`/`chunks`; B-tree en `document_id` y `created_at` para filtros. |
| 6 | Consulta y recuperación | **Query rewriting** (1 llamada LLM reescribe la pregunta) + **query expansion multi-query** (3 paráfrasis, misma llamada) → embed de cada consulta → top-k semántico sobre **children** + filtro opcional por metadatos (híbrido) → **fusión por parent** (mínima distancia, dedupe) → contenido del **parent** (small-to-big) vía `DISTINCT ON` + `COALESCE`. La generación usa la pregunta **original**. |
| 7 | Aumento del prompt | System instructions + chunks con referencia a documento + pregunta. |
| 8 | Generación de respuesta | **Gemini API** (gemini-2.5-flash), temperature 0.2, max_output_tokens acotado. |
| 9 | Verificación de respuesta | Chequeos procedurales: no vacía, longitud, citas cuando hay contexto. |

> **Nota (actualización)**: la generación migró de Gemini a **Anthropic
> Claude** (`backend/app/llm.py`: claude-fable-5 para respuestas y
> claude-haiku-4-5 para llamadas auxiliares). El módulo aplica el patrón
> **Strategy** con doble proveedor seleccionable por `LLM_PROVIDER`:
> `anthropic` (API directa con `ANTHROPIC_API_KEY`, **default**) o `vertex`
> (Vertex AI Model Garden con ADC/Workload Identity). Los embeddings siguen
> en la Gemini API (Anthropic no tiene modelo de embeddings).

## 4. Arquitectura de la solución propuesta

### 4.1. Mapeo de productos

| Componente | Producto | Justificación | Alternativas consideradas |
|---|---|---|---|
| Almacén docs | Cloud Storage + GCS FUSE CSI | Barato, montable en pods ([doc](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/cloud-storage-fuse-csi-driver)) | Filestore (caro) |
| Compute/orquestación | GKE Autopilot | Fee cubierto por free tier, facturación por pod, escala a 0 ([pricing](https://cloud.google.com/kubernetes-engine/pricing)) | GKE Standard (más ops) |
| Pipeline chunking | Kubernetes Job (Spot) | Ingesta manual de 1 doc: simple y ~gratis | Ray/KubeRay (sobredimensionado aquí; recomendado para batch masivo) |
| Embeddings + generación | Gemini API (AI Studio free tier → Vertex AI) | $0 a volumen de aprendizaje; upgrade = solo config | Gemma+vLLM self-hosted (~$450/mes GPU) |
| Vector store | Cloud SQL PG + pgvector, db-f1-micro, IP privada | Vectores + metadatos juntos; motor de la [arquitectura de referencia](https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-gke) | AlloyDB (~$500/mes); Vector Search (coste fijo alto) |
| API orquestadora | FastAPI en GKE + [conector Python Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/language-connectors) | Ligero, async | +LangChain (peso innecesario) |
| UI | Next.js en GKE (rewrite /api server-side) | Sin LB: el navegador solo habla con `web` | Cloud Run (rompe "todo en K8s") |
| Observabilidad | Cloud Logging + Monitoring + Query Insights | Estándar, free tier | SLOs custom (futuro) |
| Secretos/identidad | Secret Manager + Workload Identity | Sin claves descargables ([doc](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity)) | Env vars planas (mala práctica) |
| IaC | Terraform + manifiestos K8s | Reproducible; `destroy` para no gastar | gcloud manual |

### 4.2. Diagrama de arquitectura

Ver [`diagram.mmd`](diagram.mmd) (Mermaid). Flujos: ingesta (1–6) y consulta
(7–11), más transversales (Secret Manager, Workload Identity, Logging).

### 4.3. Descripción de la arquitectura

- **Flujo de embeddings (bajo demanda)**: `UI → POST /documents → API → (1) doc a GCS · (2) metadata pending en Cloud SQL · (3) crea Job Spot → (4) Job lee doc vía GCS FUSE → extrae/limpia/trocea → (5) vectoriza con Gemini embeddings → (6) inserta chunks+vectores y marca ready`.
- **Flujo de serving (tiempo real)**: `pregunta → API → (7) embed pregunta → (8) SELECT ... ORDER BY embedding <=> $q LIMIT 4 con filtro opcional (HNSW) → (9) prompt aumentado a Gemini → (10) respuesta → sanity checks → (11) respuesta + citas a la UI`.
- **Relaciones**: UI→API (REST) · API→GCS (SDK + Workload Identity) · API/Job→Cloud SQL (conector Python, IP privada) · API→K8s API (Jobs) · API/Job→Gemini (HTTPS, API key de Secret Manager) · GKE→Logging/Monitoring.

## 5. Recomendaciones de diseño y configuración

### 5.1. Seguridad, privacidad y cumplimiento

- **Acceso**: Workload Identity; bucket con uniform access y `public_access_prevention=enforced`.
- **Protección de datos**: Cloud SQL solo IP privada (TLS 1.3 vía conector); secretos en Secret Manager inyectados como env; cifrado gestionado por Google (CMEK = upgrade).
- **Red**: sin IP pública en la DB; sin Load Balancer (port-forward).
- ⚠️ Free tier de AI Studio puede usar prompts para mejorar productos → no subir datos sensibles; migrar a Vertex AI si cambia el requisito.
- Revisar recomendaciones de Active Assist tras el despliegue.

### 5.2. Fiabilidad

- Clúster Autopilot regional (control plane multi-zona, SLA 99.95 % incluido).
- 1 réplica por Deployment (K8s la recrea ante fallo); upgrade: 2 réplicas + PDB.
- Cloud SQL standalone con backups diarios (7 días); upgrade: instancia HA.

### 5.3. Excelencia operacional

- Logs JSON automáticos en Cloud Logging; Query Insights activado; dashboard básico de Monitoring.
- **Alerta de presupuesto ($30–50) creada antes de desplegar.**
- Terraform como única fuente de verdad.

### 5.4. Optimización de coste

- Requests ajustados (api 0.5 vCPU/1Gi, web 0.25/512Mi, chunker 0.5/512Mi); Spot en Jobs.
- Runbook escala a cero + parar Cloud SQL (`scripts/session-end.sh`).
- Sin LB; GCS Standard; Gemini free tier (token optimization al migrar a Vertex AI).

### 5.5. Eficiencia de rendimiento

- Índice HNSW (`vector_cosine_ops`, defaults m=16/ef_construction=64); top-k=4.
- Imágenes slim + Image streaming; pool SQLAlchemy 2–5 conexiones.
- Chunking 1000/150 caracteres (como el tutorial oficial).

### 5.6. Sostenibilidad

- Sin requisito; right-sizing de Autopilot y scale-to-zero minimizan huella.

## 6. Guía de despliegue

### 6.1. Prerequisitos

- APIs: `compute`, `container`, `sqladmin`, `servicenetworking`, `storage`,
  `secretmanager`, `artifactregistry`, `iam`, `cloudbuild` (Terraform las habilita).
- Herramientas: gcloud, terraform ≥1.6, kubectl, cloud-sql-proxy, psql.
- Alerta de presupuesto creada. API key de AI Studio.

### 6.2. Pasos

1. `cp terraform/terraform.tfvars.example terraform/terraform.tfvars` y rellenar.
2. `export PROJECT_ID=... && scripts/deploy.sh` (Terraform → builds → secretos →
   manifiestos → init DB).
3. `kubectl -n rag port-forward svc/web 3000:3000` → http://localhost:3000.
4. Fin de sesión: `scripts/session-end.sh` (coste ≈ $0).

## 7. Plan de validación

1. **Dry-run**: `terraform plan` sin errores; recursos `RUNNING` tras apply.
2. **Conectividad**: `/health` y `/health/db` 200; subida a GCS con WIF; port-forward OK.
3. **Índice vectorial**: `scripts/validate_db.sql` — extensión `vector`, índice HNSW, `EXPLAIN` con Index Scan.
4. **Pipeline E2E**: subir PDF → `ready` + Job `1/1` + vectores no nulos.
5. **Latencia**: `/query` < ~3 s extremo a extremo.
6. **Precisión**: pregunta respondible (cita correcta), fuera de contexto ("no lo sé"), con filtro `document_id`.
7. **Seguridad**: sin IP pública en DB, bucket no público, sin secretos en manifiestos.
8. **Coste**: Billing a las 48 h vs. proyección; alerta activa.

Ejecutable con `scripts/validate.sh` + `scripts/validate_db.sql`.

## 8. Referencias

- [RAG en GKE y Cloud SQL (arquitectura de referencia)](https://docs.cloud.google.com/architecture/rag-capable-gen-ai-app-using-gke)
- [Tutorial: RAG chatbot con GKE y Cloud Storage](https://docs.cloud.google.com/kubernetes-engine/docs/tutorials/build-rag-chatbot)
- [Blueprints RAG on GKE](https://gke-ai-labs.dev/docs/blueprints/rag-on-gke/)
- [GKE Autopilot](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview) · [seguridad](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/autopilot-security)
- [Conectores de lenguaje de Cloud SQL](https://docs.cloud.google.com/sql/docs/postgres/language-connectors) · [workflow de embeddings](https://docs.cloud.google.com/sql/docs/postgres/understand-example-embedding-workflow) · [Query Insights](https://docs.cloud.google.com/sql/docs/postgres/using-query-insights)
- [pgvector: indexación HNSW/IVFFlat](https://github.com/pgvector/pgvector#indexing)
- [GCS FUSE CSI driver](https://docs.cloud.google.com/kubernetes-engine/docs/how-to/persistent-volumes/cloud-storage-fuse-csi-driver) · [Workload Identity](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity)
- [GKE pricing / free tier](https://cloud.google.com/kubernetes-engine/pricing)
