#!/usr/bin/env bash
# Runbook "mínimo disciplinado" — inicio de sesión.
set -euo pipefail

export PROJECT_ID="${PROJECT_ID:?export PROJECT_ID=tu-proyecto}"
export REGION="${REGION:-us-central1}"
DB_INSTANCE="${DB_INSTANCE:-rag-postgres}"

echo "==> Activando Cloud SQL..."
gcloud sql instances patch "$DB_INSTANCE" \
  --activation-policy ALWAYS --project "$PROJECT_ID" --quiet

echo "==> Escalando workloads a 1 réplica..."
kubectl -n rag scale deploy api --replicas=1
kubectl -n rag scale deploy web --replicas=1

echo "==> Acceso: kubectl -n rag port-forward svc/web 3000:3000"
