#!/usr/bin/env bash
set -euo pipefail

# One-click startup for Redis + Celery + FastAPI + Next.js
# Usage:
#   ./start_all.sh
#   ./start_all.sh --no-install
#   ./start_all.sh --stop
#   ./start_all.sh --force-restart

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
WEB_DIR="$ROOT_DIR/web"
RUNTIME_DIR="$ROOT_DIR/.runtime"
LOG_DIR="$RUNTIME_DIR/logs"
PID_FILE="$RUNTIME_DIR/pids.env"

FASTAPI_PORT="${FASTAPI_PORT:-8080}"
NEXT_PORT="${NEXT_PORT:-3000}"

mkdir -p "$LOG_DIR"

log() { printf "[start_all] %s\n" "$*"; }
warn() { printf "[start_all][warn] %s\n" "$*" >&2; }
err() { printf "[start_all][error] %s\n" "$*" >&2; }

has_cmd() { command -v "$1" >/dev/null 2>&1; }

is_port_open() {
  local port="$1"
  if has_cmd lsof; then
    lsof -iTCP:"$port" -sTCP:LISTEN -Pn >/dev/null 2>&1
  else
    return 1
  fi
}

kill_port_listeners() {
  local port="$1"
  if ! has_cmd lsof; then
    warn "lsof not found; cannot force-kill port $port listeners."
    return 0
  fi
  local pids
  pids="$(lsof -ti tcp:"$port" -sTCP:LISTEN -Pn 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    log "Force stopping listeners on :$port -> $pids"
    # shellcheck disable=SC2086
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 0.3
  fi
}

# Celery spawns a process tree; --stop via pid file alone leaves orphan workers that keep old SSOT names.
stop_livehouse_celery_workers() {
  if ! has_cmd pgrep; then
    return 0
  fi
  local pids
  pids="$(pgrep -f "[c]elery -A celery_app\\.celery_app worker" 2>/dev/null || true)"
  if [[ -z "$pids" ]]; then
    return 0
  fi
  log "Stopping Celery worker process(es): $pids"
  # shellcheck disable=SC2086
  kill $pids >/dev/null 2>&1 || true
  sleep 1
  pids="$(pgrep -f "[c]elery -A celery_app\\.celery_app worker" 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    warn "Celery still running; sending SIGKILL -> $pids"
    # shellcheck disable=SC2086
    kill -9 $pids >/dev/null 2>&1 || true
    sleep 0.3
  fi
}

stop_from_pid_file() {
  if [[ ! -f "$PID_FILE" ]]; then
    log "No PID file found: $PID_FILE"
    return 0
  fi
  # shellcheck disable=SC1090
  source "$PID_FILE"
  for name in next_pid fastapi_pid celery_pid redis_pid; do
    local_pid="${!name:-}"
    if [[ -n "${local_pid}" ]] && kill -0 "${local_pid}" >/dev/null 2>&1; then
      log "Stopping ${name} (${local_pid})"
      kill "${local_pid}" >/dev/null 2>&1 || true
    fi
  done
  rm -f "$PID_FILE"
  log "Stopped services listed in PID file."
}

if [[ "${1:-}" == "--stop" ]]; then
  stop_from_pid_file
  stop_livehouse_celery_workers
  exit 0
fi

NO_INSTALL="false"
FORCE_RESTART="false"
if [[ "${1:-}" == "--no-install" ]]; then
  NO_INSTALL="true"
fi
if [[ "${1:-}" == "--force-restart" ]]; then
  FORCE_RESTART="true"
fi
if [[ "${2:-}" == "--force-restart" ]]; then
  FORCE_RESTART="true"
fi
if [[ "${1:-}" == "--force-restart" && "${2:-}" == "--no-install" ]]; then
  NO_INSTALL="true"
fi
if [[ "${1:-}" == "--no-install" && "${2:-}" == "--force-restart" ]]; then
  NO_INSTALL="true"
fi

if ! has_cmd python; then
  err "python not found"
  exit 1
fi
if ! has_cmd celery; then
  err "celery CLI not found. Install python deps first."
  exit 1
fi
if ! has_cmd redis-server; then
  warn "redis-server not found; will try to continue if Redis already running."
fi
web_uses_pnpm() {
  [[ -f "$WEB_DIR/pnpm-lock.yaml" ]] && has_cmd pnpm
}

if ! web_uses_pnpm && ! has_cmd npm; then
  err "Need pnpm (web/pnpm-lock.yaml) or npm — Node.js 18+ required for Next.js"
  exit 1
fi

if [[ "$NO_INSTALL" != "true" ]]; then
  log "Installing web dependencies..."
  if web_uses_pnpm; then
    (cd "$WEB_DIR" && pnpm install)
  else
    (cd "$WEB_DIR" && npm install)
  fi
fi

if [[ ! -f "$WEB_DIR/.env.local" && -f "$WEB_DIR/.env.example" ]]; then
  cp "$WEB_DIR/.env.example" "$WEB_DIR/.env.local"
  log "Created web/.env.local from .env.example"
fi

if [[ -f "$PID_FILE" ]]; then
  warn "Found existing PID file. Stopping old processes first."
  stop_from_pid_file
else
  stop_livehouse_celery_workers
fi

if [[ "$FORCE_RESTART" == "true" ]]; then
  kill_port_listeners "$FASTAPI_PORT"
  kill_port_listeners "$NEXT_PORT"
  log "Clearing Next.js build cache (.next)..."
  rm -rf "$WEB_DIR/.next" "$WEB_DIR/node_modules/.cache"
fi

redis_pid=""
celery_pid=""
fastapi_pid=""
next_pid=""

if is_port_open 6379; then
  log "Redis already listening on 6379, skip starting redis-server."
else
  if has_cmd redis-server; then
    log "Starting redis-server..."
    redis-server >"$LOG_DIR/redis.log" 2>&1 &
    redis_pid="$!"
    sleep 0.8
    if ! is_port_open 6379; then
      warn "Redis not listening on 6379 yet. Check $LOG_DIR/redis.log"
    fi
  else
    warn "redis-server not available and 6379 is closed. Celery may fail."
  fi
fi

log "Starting Celery worker..."
(
  cd "$ROOT_DIR"
  export OPENCV_OPENCL_RUNTIME="${OPENCV_OPENCL_RUNTIME:-disabled}"
  export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
  export LIVEHOUSE_EXECUTOR_CLASS="${LIVEHOUSE_EXECUTOR_CLASS:-general}"
  # SSOT worker_name is brain@<this>; avoid Celery %h (real machine .local hostname on macOS).
  export CELERY_WORKER_HOSTNAME="${CELERY_WORKER_HOSTNAME:-${LIVEHOUSE_EXECUTOR_CLASS}@dev}"
  celery -A celery_app.celery_app worker -l info -n "$CELERY_WORKER_HOSTNAME" >"$LOG_DIR/celery.log" 2>&1
) &
celery_pid="$!"

if is_port_open "$FASTAPI_PORT"; then
  log "Replacing existing listener on :$FASTAPI_PORT (reload gallery_server with current code)"
  kill_port_listeners "$FASTAPI_PORT"
fi
log "Starting FastAPI gallery server on :$FASTAPI_PORT ..."
# Do NOT pass ROOT_DIR as BASE_DIR; let gallery_server resolve source_dir
# from configs/livehouse.yaml (or --config override) to avoid wrong image paths.
(cd "$ROOT_DIR" && python gallery_server.py "$FASTAPI_PORT" >"$LOG_DIR/fastapi.log" 2>&1) &
fastapi_pid="$!"

if is_port_open "$NEXT_PORT"; then
  warn "Port $NEXT_PORT already in use, skip Next.js startup. Use --force-restart to replace."
else
  log "Starting Next.js on :$NEXT_PORT ..."
  if web_uses_pnpm; then
    # pnpm does not forward `run dev -- --port` like npm; use PORT for next dev.
    (cd "$WEB_DIR" && PORT="$NEXT_PORT" pnpm run dev >"$LOG_DIR/next.log" 2>&1) &
  else
    (cd "$WEB_DIR" && npm run dev -- --port "$NEXT_PORT" >"$LOG_DIR/next.log" 2>&1) &
  fi
  next_pid="$!"
fi

cat >"$PID_FILE" <<EOF
redis_pid=${redis_pid}
celery_pid=${celery_pid}
fastapi_pid=${fastapi_pid}
next_pid=${next_pid}
EOF

log "All startup commands issued."
log "Next.js:   http://127.0.0.1:${NEXT_PORT}"
log "FastAPI:   http://127.0.0.1:${FASTAPI_PORT}"
log "Queue API: http://127.0.0.1:${FASTAPI_PORT}/api/tasks/queue-backlog"
log "Logs dir:  $LOG_DIR"
log "Stop all:  ./start_all.sh --stop"
