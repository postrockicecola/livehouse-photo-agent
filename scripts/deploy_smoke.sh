#!/usr/bin/env bash
# Lightweight deploy / deep-dive smoke checks (no Redis/Celery required).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "== py_compile (Batch D/E hot paths) =="
python -m py_compile \
  services/job_executor.py \
  services/job_lifecycle.py \
  services/worker_pools.py \
  services/job_dispatch.py \
  utils/luma_brain.py \
  utils/brain_backend.py \
  inference/queue.py \
  infra/scope_quota.py \
  infra/otel_bootstrap.py \
  reliability_scenarios.py \
  scripts/eval/export_experiment_report.py

echo "== entrypoint honors LIVEHOUSE_WORKER_QUEUES =="
grep -q 'LIVEHOUSE_WORKER_QUEUES' deploy/docker-entrypoint.sh
grep -q -- '-Q' deploy/docker-entrypoint.sh

echo "== Batch E docs / hooks present =="
test -f docs/PLATFORM_SCOPE.txt
grep -q 'LIVEHOUSE_BRAIN_BACKEND' utils/brain_backend.py
grep -q 'content_digest' luma_brain_schema.sql

echo "== focused pytest =="
python -m pytest -q \
  tests/test_worker_pools.py \
  tests/test_pipeline_stage_dispatch.py \
  tests/test_inference_queue_shutdown.py \
  tests/test_reliability_chaos.py \
  tests/test_brain_backend.py \
  tests/test_scope_quota.py \
  tests/test_artifact_content_digest.py

echo "deploy_smoke: OK"
