"""Celery application bootstrap for Livehouse tasks.

Primary task envelopes: ``tasks.run_job`` (execute by ``job_id``), ``tasks.process_brain_ingested``
(ingest dispatch). Legacy shim: ``tasks.run_image_analysis`` in ``tasks.misc`` — prefer jobs + ``run_job``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# OpenCV + Haar on macOS can hang in OpenCL (uninterruptible UE); disable before any cv2 import.
os.environ.setdefault("OPENCV_OPENCL_RUNTIME", "disabled")

# Repo root on sys.path so forked workers can import root modules (e.g. ``op_kernel``)
# even when the process cwd is a session ``Previews/`` directory.
_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from celery import Celery

from utils.logging_setup import configure_logging


BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
STUCK_SCAN_ENABLED = os.getenv("LUMA_STUCK_SCAN_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
STUCK_SCAN_INTERVAL_SECONDS = max(15, int(os.getenv("LUMA_STUCK_SCAN_INTERVAL_SECONDS", "60")))
STUCK_JOB_TIMEOUT_SECONDS = max(60, int(os.getenv("LUMA_STUCK_JOB_TIMEOUT_SECONDS", str(15 * 60))))
STUCK_WORKER_TIMEOUT_SECONDS = max(30, int(os.getenv("LUMA_STUCK_WORKER_TIMEOUT_SECONDS", str(5 * 60))))
STUCK_SCAN_LIMIT = max(1, int(os.getenv("LUMA_STUCK_SCAN_LIMIT", "200")))
DISPATCH_BEAT_ENABLED = os.getenv("LUMA_DISPATCH_BEAT_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
DISPATCH_BEAT_INTERVAL_SECONDS = max(15, int(os.getenv("LUMA_DISPATCH_BEAT_INTERVAL_SECONDS", "60")))
DISPATCH_CANDIDATE_LIMIT = max(1, min(5000, int(os.getenv("LUMA_DISPATCH_CANDIDATE_LIMIT", "500"))))

celery_app = Celery(
    "livehouse_tasks",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    include=["tasks"],
)

celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=False,
    # Keep root logger; we attach structured handlers in utils.logging_setup.
    worker_hijack_root_logger=False,
)

_beat_schedule: dict = {}
if STUCK_SCAN_ENABLED:
    _beat_schedule["scan-and-requeue-stuck-jobs"] = {
        "task": "tasks.scan_and_requeue_stuck_jobs",
        "schedule": STUCK_SCAN_INTERVAL_SECONDS,
        "kwargs": {
            "stale_after_seconds": STUCK_JOB_TIMEOUT_SECONDS,
            "worker_stale_after_seconds": STUCK_WORKER_TIMEOUT_SECONDS,
            "limit": STUCK_SCAN_LIMIT,
        },
    }
if DISPATCH_BEAT_ENABLED:
    _beat_schedule["dispatch-runnable-jobs"] = {
        "task": "tasks.dispatch_runnable_jobs",
        "schedule": DISPATCH_BEAT_INTERVAL_SECONDS,
        "kwargs": {"candidate_limit": DISPATCH_CANDIDATE_LIMIT},
    }
if _beat_schedule:
    celery_app.conf.beat_schedule = _beat_schedule

configure_logging()
try:
    from infra.otel_bootstrap import configure_otel_from_env

    configure_otel_from_env()
except Exception:
    pass
