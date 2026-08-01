#!/usr/bin/env bash
# Runbook "mínimo disciplinado" — fin de sesión: coste ≈ $0 de compute.
set -euo pipefail

export PROJECT_ID="${PROJECT_ID:?export PROJECT_ID=tu-proyecto}"
export REGION="${REGION:-us-central1}"
DB_INSTANCE="${DB_INSTANCE:-rag-postgres}"

echo "==> Escalando workloads a 0 réplicas..."
kubectl -n rag scale deploy --replicas=0 --all

echo "==> Parando Cloud SQL (deja de facturar compute; storage sigue ~\$2/mes)..."
gcloud sql instances patch "$DB_INSTANCE" \
  --activation-policy NEVER --project "$PROJECT_ID" --quiet

echo "==> Fin de sesión. El fee del clúster Autopilot lo cubre el free tier."
