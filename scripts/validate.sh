#!/usr/bin/env bash
# Plan de validación (Fase 3) — checks 1, 2, 4, 5, 6 y 7.
# Ejecutar tras el despliegue. Requiere: gcloud, kubectl, curl, jq (opcional).
set -uo pipefail

export PROJECT_ID="${PROJECT_ID:?export PROJECT_ID=tu-proyecto}"
export REGION="${REGION:-us-central1}"
DB_INSTANCE="${DB_INSTANCE:-rag-postgres}"
BUCKET="${BUCKET:?export BUCKET=nombre-del-bucket}"
FAILED=0

check() { # check <descripción> <comando...>
  echo -n "▸ $1... "
  shift
  if "$@" >/dev/null 2>&1; then echo "OK"; else echo "FALLO"; FAILED=1; fi
}

echo "== 1. Recursos desplegados =="
check "Clúster GKE RUNNING"   gcloud container clusters list --region "$REGION" --filter "status=RUNNING" --format "value(name)"
check "Instancia Cloud SQL"   gcloud sql instances describe "$DB_INSTANCE" --format "value(name)"
check "Bucket de documentos"  gcloud storage buckets describe "gs://$BUCKET" --format "value(name)"

echo "== 2. Conectividad =="
kubectl -n rag port-forward svc/api 18000:8000 >/dev/null 2>&1 &
PF_PID=$!; sleep 3
check "API /health"           curl -sf http://localhost:18000/health
check "API /health/db"        curl -sf http://localhost:18000/health/db

echo "== 4. Pipeline E2E (ingesta de prueba) =="
echo "Documento de prueba del pipeline RAG. Google Cloud es una nube." > /tmp/rag-test.txt
RESP=$(curl -sf -F "file=@/tmp/rag-test.txt" http://localhost:18000/documents) \
  && echo "  subido: $RESP" || { echo "  FALLO la subida"; FAILED=1; }
sleep 45  # dar tiempo al Job de chunking
check "Job de chunking completado" kubectl -n rag get jobs -o jsonpath='{.items[0].status.succeeded}' | grep -q 1

echo "== 5/6. Latencia y precisión de retrieval =="
START=$(date +%s%N)
ANSWER=$(curl -sf -X POST http://localhost:18000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"¿Qué es Google Cloud?"}')
END=$(date +%s%N)
echo "  latencia extremo a extremo: $(( (END - START) / 1000000 )) ms (objetivo < 3000 ms)"
[ -n "$ANSWER" ] && echo "  respuesta: ${ANSWER:0:200}..." || { echo "  FALLO /query"; FAILED=1; }
echo "  (fuera de contexto)"
curl -sf -X POST http://localhost:18000/query \
  -H "Content-Type: application/json" \
  -d '{"question":"¿Cuál es la capital de Marte?"}' | head -c 300; echo

kill $PF_PID 2>/dev/null

echo "== 7. Seguridad =="
check "Cloud SQL sin IP pública" bash -c \
  "gcloud sql instances describe $DB_INSTANCE --format 'value(settings.ipConfiguration.ipv4Enabled)' | grep -qi false"
check "Bucket con acceso público bloqueado" bash -c \
  "gcloud storage buckets describe gs://$BUCKET --format 'value(public_access_prevention)' | grep -q enforced"
check "Sin secretos en manifiestos" bash -c \
  "! grep -rEi '(api[_-]?key|password)\s*[:=]\s*[A-Za-z0-9]{16,}' k8s/*.yaml"

echo
if [ "$FAILED" -eq 0 ]; then echo "TODAS LAS VALIDACIONES PASARON"; else echo "HAY VALIDACIONES FALLIDAS"; exit 1; fi
