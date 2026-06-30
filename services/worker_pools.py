"""
Worker pools / executor classes — logical isolation model for Celery workers.

Admission slots per SSOT worker row are sized by :mod:`infra.worker_capacity` (Celery concurrency +
optional env clamps including inference ceilings); dispatch headroom aggregates ``capacity minus live
job-row counts`` per ONLINE worker — see ``utils.luma_brain.executor_pool_headroom_for_dispatch``.

Why separate pools?
---------------------
Inference (GPU/VLM), ingest/prepare (filesystem churn), artifact/report generation (IO-heavy HTML),
and orchestration/FINALIZE steps (cheap DB transitions + chaining) contend for different resources.
Running them on one homogeneous fleet yields noisy latency (best-effort Celery). Mapping jobs to
executor classes lets operators pin workloads to processes via ``LIVEHOUSE_EXECUTOR_CLASS`` and keeps
the SSOT schema aligned with real infra boundaries — without requiring multiple clusters yet.

Policy (minimal MVP):
---------------------
- Jobs derive a **required** executor class from ``(job_type, stage_name)``, optionally overridden by
  ``payload_json.executor_class`` or ``payload_json.worker_pool``.
- Workers advertise an executor class in ``workers.worker_type`` (same column name historically used for
  transport-ish labels — treated here as **executor pool id**).
- **Legacy / omnivore pools** match every job: ``celery``, ``generic``, ``general``, ``*`` (compat).
- **Dedicated pools** match only the same class unless listed above.

Celery stays one broker; isolation is cooperative (workers skip claims / dispatch skips enqueue when
pool headroom is zero).
"""
from __future__ import annotations

import json
from typing import Any

from services.pipeline_stages import STAGE_JOB_TYPE

# Canonical executor classes referenced by routing and dashboards.
EXECUTOR_ORCHESTRATOR = "orchestrator"
EXECUTOR_INFERENCE = "inference"
EXECUTOR_REPORTING = "reporting"
EXECUTOR_INGEST = "ingest"
# Single-process default: accepts every required class (back-compat with one Celery worker).
EXECUTOR_GENERAL = "general"

KNOWN_EXECUTOR_CLASSES: tuple[str, ...] = (
    EXECUTOR_GENERAL,
    EXECUTOR_ORCHESTRATOR,
    EXECUTOR_INFERENCE,
    EXECUTOR_REPORTING,
    EXECUTOR_INGEST,
)

# Pools jobs route on (subset of KNOWN — excludes GENERAL omnivore tag).
JOB_ROUTING_EXECUTOR_CLASSES: tuple[str, ...] = (
    EXECUTOR_ORCHESTRATOR,
    EXECUTOR_INFERENCE,
    EXECUTOR_REPORTING,
    EXECUTOR_INGEST,
)

# Worker rows created before pool semantics or generic Celery probes — behave like traffic sponges.
_LEGACY_WORKER_POOL_TAGS: frozenset[str] = frozenset({"celery", "generic", ""})

# Explicit pools that accept any required executor (operators pin one fleet for demos).
_UNIVERSAL_WORKER_POOLS: frozenset[str] = frozenset({EXECUTOR_GENERAL, "*"})


def normalize_executor_class(raw: str | None) -> str:
    s = str(raw or "").strip().lower()
    return s if s else EXECUTOR_GENERAL


def _payload_executor_override(payload_json: Any) -> str | None:
    if payload_json is None:
        return None
    try:
        if isinstance(payload_json, str):
            p = json.loads(payload_json)
        elif isinstance(payload_json, dict):
            p = payload_json
        else:
            return None
        if not isinstance(p, dict):
            return None
        v = p.get("executor_class") if "executor_class" in p else p.get("worker_pool")
        if v is None:
            return None
        vs = str(v).strip().lower()
        return vs if vs else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def required_executor_class_for_job(job_row: dict[str, Any]) -> str:
    """
    Resolve required executor pool for a job row (dispatch + admission SSOT).

    Order: payload override → (job_type, stage_name) map → inference default for unknown shapes.
    """
    ov = _payload_executor_override(job_row.get("payload_json"))
    if ov:
        return normalize_executor_class(ov)

    jt = str(job_row.get("job_type") or "").strip().upper()
    if jt in {"ANALYZE_SESSION", "ANALYZE_PATH"}:
        return EXECUTOR_INFERENCE

    if jt == STAGE_JOB_TYPE:
        stage = str(job_row.get("stage_name") or "").strip().upper()
        if stage == "PREPARE_INPUT":
            return EXECUTOR_INGEST
        if stage in {"STAGE1_FILTER", "STAGE2_FAST_SCORE", "STAGE3_VLM"}:
            return EXECUTOR_INFERENCE
        if stage == "WRITE_ARTIFACT":
            return EXECUTOR_REPORTING
        if stage == "FINALIZE":
            return EXECUTOR_ORCHESTRATOR

    return EXECUTOR_INFERENCE


def worker_pool_accepts_job(worker_executor_pool: str | None, required_executor: str) -> bool:
    """Whether a worker registered with ``worker_executor_pool`` may claim ``required_executor``."""
    w = normalize_executor_class(worker_executor_pool)
    r = normalize_executor_class(required_executor)

    if w in _LEGACY_WORKER_POOL_TAGS:
        return True
    if w in _UNIVERSAL_WORKER_POOLS:
        return True
    return w == r


def split_legacy_and_specific_capacity(
    *,
    worker_type_raw: str | None,
    capacity: int,
    inflight: int,
) -> tuple[int, str | None]:
    """
    Returns ``(free_slots, specific_pool_or_none)``.

    Legacy/universal pools contribute only to the wildcard bucket (counts toward every executor).
    Dedicated pools contribute only to their tag.
    """
    cap = max(0, int(capacity or 0))
    inf = max(0, int(inflight or 0))
    free_slots = max(0, cap - inf)
    wt = normalize_executor_class(worker_type_raw)
    if wt in _LEGACY_WORKER_POOL_TAGS or wt in _UNIVERSAL_WORKER_POOLS:
        return free_slots, None
    if wt in KNOWN_EXECUTOR_CLASSES and wt != EXECUTOR_GENERAL:
        return free_slots, wt
    # Unknown labels route to inference-shaped work by convention.
    return free_slots, EXECUTOR_INFERENCE
