#!/usr/bin/env bash
# Despliegue end-to-end (secuencia 6.7 de la guía de arquitectura).
# Prerequisitos: gcloud, terraform, kubectl, docker|cloudbuild, y
# terraform/terraform.tfvars rellenado (ver terraform.tfvars.example).
set -euo pipefail
cd "$(dirname "$0")/.."

export PROJECT_ID="${PROJECT_ID:?export PROJECT_ID=tu-proyecto}"
export REGION="${REGION:-us-central1}"

# Modelos (mismos defaults que .env.example). Se inyectan en los
# Deployments/Jobs vía envsubst. OJO: EMBEDDING_DIMS debe coincidir con
# vector(N) de scripts/init_db.sql (HNSW soporta máx. 2000 dims).
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-text-embedding-005}"
export EMBEDDING_DIMS="${EMBEDDING_DIMS:-1536}"
# Generación: Anthropic Claude con doble proveedor (patrón Strategy,
# backend/app/llm.py): "anthropic" = API directa (requiere el secreto
# anthropic-api-key en Secret Manager); "vertex" = Model Garden (requiere el
# modelo habilitado en la consola — terraform solo da la API de Vertex y el
# rol roles/aiplatform.user a la GSA).
export LLM_PROVIDER="${LLM_PROVIDER:-anthropic}"
export GEN_MODEL="${GEN_MODEL:-claude-fable-5}"
export GENERAL_MODEL="${GENERAL_MODEL:-claude-fable-5}"
export FAST_MODEL="${FAST_MODEL:-claude-haiku-4-5}"
export ANTHROPIC_VERTEX_REGION="${ANTHROPIC_VERTEX_REGION:-global}"
# Small-to-big (pre-retrieval) + ventana deslizante (ver .env.example).
export SMALL_TO_BIG="${SMALL_TO_BIG:-true}"
export PARENT_CHUNK_SIZE="${PARENT_CHUNK_SIZE:-1024}"
export PARENT_CHUNK_OVERLAP="${PARENT_CHUNK_OVERLAP:-128}"
export SMALL_CHUNK_SIZE="${SMALL_CHUNK_SIZE:-256}"
export SMALL_CHUNK_OVERLAP="${SMALL_CHUNK_OVERLAP:-50}"
# Query rewriting + query expansion multi-query (pre-retrieval, lado consulta).
export QUERY_REWRITE="${QUERY_REWRITE:-true}"
export QUERY_EXPANSION="${QUERY_EXPANSION:-true}"
export EXPANSION_VARIANTS="${EXPANSION_VARIANTS:-3}"

echo "==> [1/7] terraform apply (15-20 min la primera vez)..."
terraform -chdir=terraform init
terraform -chdir=terraform apply -auto-approve

export BUCKET_NAME="$(terraform -chdir=terraform output -raw bucket_name)"
export INSTANCE_CONNECTION_NAME="$(terraform -chdir=terraform output -raw instance_connection_name)"
export REPO_URL="$(terraform -chdir=terraform output -raw repo_url)"
export GCP_SA_EMAIL="$(terraform -chdir=terraform output -raw gcp_sa_email)"
CLUSTER="$(terraform -chdir=terraform output -raw cluster_name)"

echo "==> [2/7] credenciales del clúster..."
gcloud container clusters get-credentials "$CLUSTER" --region "$REGION" --project "$PROJECT_ID"

echo "==> [3/7] build de imágenes (Cloud Build)..."
gcloud builds submit backend  --tag "$REPO_URL/api:1.0"     --project "$PROJECT_ID"
gcloud builds submit chunker  --tag "$REPO_URL/chunker:1.0" --project "$PROJECT_ID"
gcloud builds submit frontend --tag "$REPO_URL/web:1.0"     --project "$PROJECT_ID"

echo "==> [4/7] secretos de K8s desde Secret Manager..."
kubectl create namespace rag --dry-run=client -o yaml | kubectl apply -f -
SECRET_ARGS=(
  --from-literal=GEMINI_API_KEY="$(gcloud secrets versions access latest --secret=gemini-api-key)"
  --from-literal=DB_PASS="$(gcloud secrets versions access latest --secret=db-password)"
)
if [ "$LLM_PROVIDER" = "anthropic" ]; then
  SECRET_ARGS+=(--from-literal=ANTHROPIC_API_KEY="$(gcloud secrets versions access latest --secret=anthropic-api-key)")
fi
kubectl -n rag create secret generic rag-secrets \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> [5/7] manifiestos (envsubst → kubectl apply)..."
for f in namespace serviceaccounts rbac deploy-api deploy-web services; do
  envsubst < "k8s/$f.yaml" | kubectl apply -f -
done

echo "==> [6/7] init de la base de datos (requiere cloud-sql-proxy)..."
if command -v cloud-sql-proxy >/dev/null; then
  cloud-sql-proxy "$INSTANCE_CONNECTION_NAME" --port 5432 &
  PROXY_PID=$!
  sleep 3
  PGPASSWORD="$(gcloud secrets versions access latest --secret=db-password)" \
    psql "host=127.0.0.1 port=5432 dbname=ragdb user=app" -f scripts/init_db.sql
  kill $PROXY_PID
else
  echo "    cloud-sql-proxy no instalado; ejecuta scripts/init_db.sql a mano (ver README)."
fi

echo "==> [7/7] listo. Acceso sin Load Balancer:"
echo "    kubectl -n rag port-forward svc/web 3000:3000"
echo "    # abrir http://localhost:3000"
