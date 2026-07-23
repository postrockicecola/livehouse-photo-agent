#!/usr/bin/env sh
set -eu

CONFIG="${LIVEHOUSE_CONFIG:-configs/livehouse.docker.yaml}"
export LIVEHOUSE_CONFIG="$CONFIG"

wait_redis() {
  url="${CELERY_BROKER_URL:-redis://redis:6379/0}"
  hostport="${url#redis://}"
  hostport="${hostport%%/*}"
  host="${hostport%%:*}"
  port="${hostport##*:}"
  if [ "$host" = "$port" ]; then
    port=6379
  fi
  echo "[entrypoint] waiting for redis at ${host}:${port}..."
  i=0
  while [ "$i" -lt 60 ]; do
    if python -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('${host}', int('${port}'))); s.close()" 2>/dev/null; then
      echo "[entrypoint] redis is up"
      return 0
    fi
    i=$((i + 1))
    sleep 1
  done
  echo "[entrypoint] redis not reachable after 60s" >&2
  exit 1
}

case "${1:-}" in
  api)
    wait_redis
    exec python gallery_server.py --config "$CONFIG" 8080
    ;;
  celery-worker)
    wait_redis
    pool="${LIVEHOUSE_EXECUTOR_CLASS:-general}"
    queues="${LIVEHOUSE_WORKER_QUEUES:-celery}"
    conc="${LIVEHOUSE_WORKER_CONCURRENCY:-}"
    set -- celery -A celery_app.celery_app worker -l info -n "${pool}@%h" -Q "${queues}"
    if [ -n "$conc" ]; then
      set -- "$@" --concurrency="$conc"
    fi
    exec "$@"
    ;;
  celery-beat)
    wait_redis
    exec celery -A celery_app.celery_app beat -l info
    ;;
  *)
    exec "$@"
    ;;
esac
