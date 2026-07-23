#!/usr/bin/env bash
# Lightweight deploy / deep-dive smoke checks (no Redis/Celery required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== py_compile (Batch D hot paths) =="
python -m py_compile \
  services/job_executor.py \
  services/job_lifecycle.py \
  services/worker_pools.py \
  services/job_dispatch.py \
  utils/luma_brain.py \
  inference/queue.py \
  reliability_scenarios.py

echo "== entrypoint honors LIVEHOUSE_WORKER_QUEUES =="
grep -q 'LIVEHOUSE_WORKER_QUEUES' deploy/docker-entrypoint.sh
grep -q -- '-Q' deploy/docker-entrypoint.sh

echo "== focused pytest =="
python -m pytest -q \
  tests/test_worker_pools.py \
  tests/test_pipeline_stage_dispatch.py \
  tests/test_inference_queue_shutdown.py \
  tests/test_reliability_chaos.py

echo "deploy_smoke: OK"
