#!/usr/bin/env bash
# Test LOCAL del RAG completo (sin tocar GCP):
#   pgvector en Docker + backend (uvicorn :8000) + frontend (next dev :3000)
#
# Uso:
#   scripts/local-test.sh start         # levanta todo y muestra los logs EN VIVO
#                                       # (Ctrl+C solo deja de verlos; los servicios siguen)
#   scripts/local-test.sh start -d      # levanta todo en background (sin logs en vivo)
#   scripts/local-test.sh stop          # para todo (API, web y contenedor; conserva datos)
#   scripts/local-test.sh restart       # stop + start (también admite -d)
#   scripts/local-test.sh logs [api|web|chunker]  # logs en vivo de uno o de todos
#
# Config: variable de entorno > .env (raíz) > backend/.env (ver .env.example).
set -euo pipefail
cd "$(dirname "$0")/.."

STATE_DIR=".local-test"   # PIDs y logs (gitignored)
PGPORT="${PGPORT:-55432}" # 55432 para no chocar con un Postgres local en 5432
CONTAINER="rag-pgvector"

load_config() {
  _SAVED_GEMINI_API_KEY="${GEMINI_API_KEY:-}"
  _SAVED_EMBEDDING_MODEL="${EMBEDDING_MODEL:-}"
  _SAVED_EMBEDDING_DIMS="${EMBEDDING_DIMS:-}"
  _SAVED_GEN_MODEL="${GEN_MODEL:-}"
  _SAVED_SMALL_TO_BIG="${SMALL_TO_BIG:-}"
  _SAVED_PARENT_CHUNK_SIZE="${PARENT_CHUNK_SIZE:-}"
  _SAVED_PARENT_CHUNK_OVERLAP="${PARENT_CHUNK_OVERLAP:-}"
  _SAVED_SMALL_CHUNK_SIZE="${SMALL_CHUNK_SIZE:-}"
  _SAVED_SMALL_CHUNK_OVERLAP="${SMALL_CHUNK_OVERLAP:-}"
  _SAVED_QUERY_REWRITE="${QUERY_REWRITE:-}"
  _SAVED_QUERY_EXPANSION="${QUERY_EXPANSION:-}"
  _SAVED_EXPANSION_VARIANTS="${EXPANSION_VARIANTS:-}"
  _SAVED_LLM_PROVIDER="${LLM_PROVIDER:-}"
  _SAVED_ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
  for envfile in .env backend/.env; do
    if [ -f "$envfile" ]; then
      echo "==> cargando $envfile"
      set -a; source "$envfile"; set +a
    fi
  done
  GEMINI_API_KEY="${_SAVED_GEMINI_API_KEY:-${GEMINI_API_KEY:-}}"
  EMBEDDING_MODEL="${_SAVED_EMBEDDING_MODEL:-${EMBEDDING_MODEL:-text-embedding-005}}"
  EMBEDDING_DIMS="${_SAVED_EMBEDDING_DIMS:-${EMBEDDING_DIMS:-1536}}"
  GEN_MODEL="${_SAVED_GEN_MODEL:-${GEN_MODEL:-gemini-2.5-flash}}"
  SMALL_TO_BIG="${_SAVED_SMALL_TO_BIG:-${SMALL_TO_BIG:-true}}"
  PARENT_CHUNK_SIZE="${_SAVED_PARENT_CHUNK_SIZE:-${PARENT_CHUNK_SIZE:-1024}}"
  PARENT_CHUNK_OVERLAP="${_SAVED_PARENT_CHUNK_OVERLAP:-${PARENT_CHUNK_OVERLAP:-128}}"
  SMALL_CHUNK_SIZE="${_SAVED_SMALL_CHUNK_SIZE:-${SMALL_CHUNK_SIZE:-256}}"
  SMALL_CHUNK_OVERLAP="${_SAVED_SMALL_CHUNK_OVERLAP:-${SMALL_CHUNK_OVERLAP:-50}}"
  QUERY_REWRITE="${_SAVED_QUERY_REWRITE:-${QUERY_REWRITE:-true}}"
  QUERY_EXPANSION="${_SAVED_QUERY_EXPANSION:-${QUERY_EXPANSION:-true}}"
  EXPANSION_VARIANTS="${_SAVED_EXPANSION_VARIANTS:-${EXPANSION_VARIANTS:-3}}"
  GEN_MODEL="${GEN_MODEL:-claude-fable-5}"
  GENERAL_MODEL="${GENERAL_MODEL:-claude-fable-5}"
  FAST_MODEL="${FAST_MODEL:-claude-haiku-4-5}"
  LLM_PROVIDER="${_SAVED_LLM_PROVIDER:-${LLM_PROVIDER:-anthropic}}"
  ANTHROPIC_API_KEY="${_SAVED_ANTHROPIC_API_KEY:-${ANTHROPIC_API_KEY:-}}"
  ANTHROPIC_VERTEX_REGION="${ANTHROPIC_VERTEX_REGION:-global}"
  export GEMINI_API_KEY="${GEMINI_API_KEY:?define GEMINI_API_KEY en el entorno o en .env (ver .env.example)}"
  if [ "$LLM_PROVIDER" = "anthropic" ]; then
    export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:?LLM_PROVIDER=anthropic: define ANTHROPIC_API_KEY en el entorno o en .env (ver .env.example)}"
  elif [ "$LLM_PROVIDER" = "vertex" ]; then
    export PROJECT_ID="${PROJECT_ID:?LLM_PROVIDER=vertex: define PROJECT_ID en el entorno o en .env (lo usa AnthropicVertex; ver .env.example)}"
  else
    echo "ERROR: LLM_PROVIDER='$LLM_PROVIDER' no válido (usa 'anthropic' o 'vertex')" >&2
    exit 1
  fi
  export EMBEDDING_MODEL EMBEDDING_DIMS GEN_MODEL GENERAL_MODEL FAST_MODEL LLM_PROVIDER ANTHROPIC_API_KEY ANTHROPIC_VERTEX_REGION
  export SMALL_TO_BIG PARENT_CHUNK_SIZE PARENT_CHUNK_OVERLAP SMALL_CHUNK_SIZE SMALL_CHUNK_OVERLAP
  export QUERY_REWRITE QUERY_EXPANSION EXPANSION_VARIANTS
  export DATABASE_URL="postgresql+pg8000://app:app@127.0.0.1:${PGPORT}/ragdb"
  export BUCKET_NAME=""   # vacío = modo local (sin GCS ni Jobs de K8s)
  echo "==> modelos: embeddings=$EMBEDDING_MODEL ($EMBEDDING_DIMS dims) · generación=$GEN_MODEL · auxiliar=$FAST_MODEL"
  if [ "$LLM_PROVIDER" = "vertex" ]; then
    echo "==> anthropic vía Vertex AI Model Garden: región=$ANTHROPIC_VERTEX_REGION · proyecto=$PROJECT_ID (ADC: gcloud auth application-default login)"
  else
    echo "==> anthropic vía API directa (ANTHROPIC_API_KEY)"
  fi
  echo "==> chunking: small_to_big=$SMALL_TO_BIG · parent=$PARENT_CHUNK_SIZE/$PARENT_CHUNK_OVERLAP · child=$SMALL_CHUNK_SIZE/$SMALL_CHUNK_OVERLAP"
  echo "==> query opt: rewrite=$QUERY_REWRITE · expansion=$QUERY_EXPANSION (x$EXPANSION_VARIANTS)"
}

db_up() {
  echo "==> [1/5] PostgreSQL + pgvector (127.0.0.1:${PGPORT})..."
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "    contenedor ya en marcha"
  elif docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    docker start "$CONTAINER" >/dev/null
  else
    docker run -d --name "$CONTAINER" -p "${PGPORT}:5432" \
      -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app -e POSTGRES_DB=ragdb \
      pgvector/pgvector:pg16 >/dev/null
  fi
  until docker exec "$CONTAINER" pg_isready -U app -d ragdb >/dev/null 2>&1; do sleep 1; done

  echo "==> [2/5] esquema (scripts/init_db.sql)..."
  docker exec -i "$CONTAINER" psql -U app -d ragdb -v ON_ERROR_STOP=1 -q < scripts/init_db.sql
}

ensure_venv() {
  echo "==> [3/5] venv de Python..."
  if [ ! -x backend/.venv/bin/python ]; then
    python3 -m venv backend/.venv 2>/dev/null || python3 -m venv --without-pip backend/.venv
    backend/.venv/bin/pip --version >/dev/null 2>&1 || \
      curl -sSL https://bootstrap.pypa.io/get-pip.py | backend/.venv/bin/python - >/dev/null
    backend/.venv/bin/pip install -q -r backend/requirements.txt -r chunker/requirements.txt
  fi
}

wait_url() { # wait_url <url> <max_segundos>
  for _ in $(seq 1 "$2"); do
    curl -sf "$1" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

svc_running() { # svc_running <api|web>
  [ -f "$STATE_DIR/$1.pid" ] && kill -0 "$(cat "$STATE_DIR/$1.pid")" 2>/dev/null
}

start_all() {
  load_config
  mkdir -p "$STATE_DIR"
  STATE_DIR_ABS="$(pwd)/$STATE_DIR"
  db_up
  ensure_venv

  echo "==> [4/5] backend (http://127.0.0.1:8000)..."
  if svc_running api; then
    echo "    ya estaba corriendo (pid $(cat "$STATE_DIR/api.pid"))"
    # Aviso anti-código-viejo: si hay .py más nuevos que el proceso, avisar.
    if [ -n "$(find backend/app -name '*.py' -newer "$STATE_DIR/api.pid" -print -quit 2>/dev/null)" ]; then
      echo "    ⚠️  ATENCIÓN: el backend corre CÓDIGO ANTIGUO (hay .py modificados después)."
      echo "    ⚠️  Ejecuta: $0 restart"
    fi
  else
    (
      cd backend
      setsid nohup .venv/bin/python -m uvicorn app.main:app \
        --host 127.0.0.1 --port 8000 > "$STATE_DIR_ABS/api.log" 2>&1 < /dev/null &
      echo $! > "$STATE_DIR_ABS/api.pid"
    )
    wait_url http://127.0.0.1:8000/health/db 20 || {
      echo "ERROR: el backend no arrancó; ver $STATE_DIR/api.log"; exit 1; }
  fi
  curl -sf http://127.0.0.1:8000/health/db && echo

  # Asegurar que los 3 logs existen antes de hacer tail (api, web y chunker).
  touch "$STATE_DIR/api.log" "$STATE_DIR/web.log" "$STATE_DIR/chunker.log"

  echo "==> [5/5] frontend (http://localhost:3000)..."
  [ -d frontend/node_modules ] || (cd frontend && npm install --no-audit --no-fund)
  if svc_running web; then
    echo "    ya estaba corriendo (pid $(cat "$STATE_DIR/web.pid"))"
  else
    (
      cd frontend
      setsid nohup npm run dev > "$STATE_DIR_ABS/web.log" 2>&1 < /dev/null &
      echo $! > "$STATE_DIR_ABS/web.pid"
    )
    wait_url http://localhost:3000 40 || {
      echo "ERROR: el frontend no arrancó; ver $STATE_DIR/web.log"; exit 1; }
  fi

  echo
  echo "TODO LEVANTADO ✅"
  echo "  UI:    http://localhost:3000"
  echo "  API:   http://127.0.0.1:8000/docs"
  echo "  logs:  $STATE_DIR/api.log · $STATE_DIR/web.log · $STATE_DIR/chunker.log"
  echo "  parar: scripts/local-test.sh stop"

  if [ "${1:-}" != "-d" ] && [ "${1:-}" != "--detach" ]; then
    echo
    echo "==> LOGS EN VIVO de api + web + chunker (Ctrl+C solo deja de verlos; los servicios SIGUEN corriendo)"
    exec tail -f "$STATE_DIR/api.log" "$STATE_DIR/web.log" "$STATE_DIR/chunker.log"
  fi
}

stop_all() {
  echo "==> parando servicios locales..."
  for svc in api web; do
    pidfile="$STATE_DIR/$svc.pid"
    if [ -f "$pidfile" ]; then
      pid="$(cat "$pidfile")"
      if kill -0 "$pid" 2>/dev/null; then
        # Los procesos se lanzaron con setsid: matar el grupo mata también
        # a los hijos (p. ej. next-server).
        kill -TERM -- -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        echo "    $svc (pid $pid) parado"
      fi
      rm -f "$pidfile"
    fi
  done
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    docker stop "$CONTAINER" >/dev/null
    echo "    contenedor $CONTAINER parado (datos conservados; borrar con: docker rm -f $CONTAINER)"
  fi
  echo "TODO PARADO ✅"
}

show_logs() {
  case "${1:-}" in
    api|web|chunker)
      logfile="$STATE_DIR/$1.log"
      if [ ! -f "$logfile" ]; then
        echo "No existe $logfile; ejecuta primero: $0 start"
        exit 1
      fi
      echo "==> tail -f $logfile (Ctrl+C para salir)"
      exec tail -f "$logfile"
      ;;
    "")
      if [ ! -f "$STATE_DIR/api.log" ] && [ ! -f "$STATE_DIR/web.log" ] && [ ! -f "$STATE_DIR/chunker.log" ]; then
        echo "No hay logs todavía; ejecuta primero: $0 start"
        exit 1
      fi
      echo "==> logs en vivo de api + web + chunker (Ctrl+C para salir)"
      touch "$STATE_DIR/api.log" "$STATE_DIR/web.log" "$STATE_DIR/chunker.log"
      exec tail -f "$STATE_DIR/api.log" "$STATE_DIR/web.log" "$STATE_DIR/chunker.log"
      ;;
    *)
      echo "Uso: $0 logs [api|web|chunker]"
      exit 1
      ;;
  esac
}

case "${1:-}" in
  start)   start_all "${2:-}" ;;
  stop)    stop_all ;;
  restart) stop_all; start_all "${2:-}" ;;
  logs)    show_logs "${2:-}" ;;
  *)
    echo "Uso: $0 {start [-d]|stop|restart [-d]|logs [api|web|chunker]}"
    exit 1
    ;;
esac
