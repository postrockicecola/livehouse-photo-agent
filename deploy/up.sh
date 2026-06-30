#!/usr/bin/env bash
# Start the local "cluster" (Compose). Run from repo root or deploy/.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
ENV_FILE="$ROOT/deploy/.env"

if [[ ! -f "$ENV_FILE" && -f "$ROOT/deploy/.env.example" ]]; then
  cp "$ROOT/deploy/.env.example" "$ENV_FILE"
  echo "[deploy] created $ENV_FILE from .env.example — edit LUMA_ARCHIVE_HOST_PATH if needed."
fi

mkdir -p /tmp/livehouse-sd-empty

cd "$ROOT"
exec docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
