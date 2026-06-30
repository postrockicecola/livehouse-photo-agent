"""
Advertised Celery-worker admission capacity (``workers.capacity`` SSOT).

Binds together:
    - **Process concurrency** — Celery prefork pool size (real parallel ``tasks.run_job`` executions).
      Detected via ``CELERY_WORKER_CONCURRENCY`` or the Celery ``Worker.concurrency`` handle.
    - **Operator clamp** — ``LIVEHOUSE_WORKER_ADMISSION_CAP`` ceilings every pool.
    - **Per executor class** — ``LIVEHOUSE_WORKER_POOL_CAPS`` as ``infer=4,ingest=8,...`` keys are
      normalized like ``services.worker_pools.normalize_executor_class``.
    - **Inference-side bound** — ``LIVEHOUSE_WORKER_INFERENCE_PROVIDER_SLOTS`` expresses the dominant
      VLM / HTTP concurrency the host can absorb (YAML ``model.max_concurrent_requests`` is the
      source of truth in config; ops should mirror it here because beat/API processes do not read
      the inference queue internals of every worker).

``workers.inflight`` is kept as a telemetry mirror updated from live job counts; admission and dispatch
headroom read **job rows** (:func:`utils.luma_brain.count_active_jobs_for_worker`).
"""
from __future__ import annotations

import os
from typing import Any

from services.worker_pools import EXECUTOR_GENERAL, EXECUTOR_INFERENCE, normalize_executor_class


def resolve_celery_concurrency(worker_sender: Any | None = None) -> int:
    """
    Best-effort Celery concurrency for this interpreter / worker boot.

    ``celery multi`` / systemd often exports ``CELERY_WORKER_CONCURRENCY``; the ``sender`` hook covers
    in-process callers (early ``worker_process_init``).
    """
    raw = os.environ.get("CELERY_WORKER_CONCURRENCY")
    if raw is not None and str(raw).strip() != "":
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    if worker_sender is not None:
        c = getattr(worker_sender, "concurrency", None)
        if c is not None:
            try:
                return max(1, int(c))
            except (TypeError, ValueError):
                pass
    return 1


def _parse_executor_pool_caps(raw: str | None) -> dict[str, int]:
    """``inference=4,ingest=8`` → normalized executor key → nonnegative int."""
    if raw is None or not str(raw).strip():
        return {}
    out: dict[str, int] = {}
    for part in str(raw).split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        ks = normalize_executor_class(k.strip())
        if not ks:
            continue
        try:
            out[ks] = max(0, int(v.strip()))
        except ValueError:
            continue
    return out


def resolve_advertised_worker_capacity(*, worker_type: str | None, celery_concurrency: int) -> int:
    """
    Integer slots stored on ``workers.capacity`` — upper bound on concurrent admitted **jobs**
    executing on one SSOT worker row (one Celery process identity).
    """
    wt = normalize_executor_class(worker_type)
    slots = max(1, int(celery_concurrency))

    cap_env = os.environ.get("LIVEHOUSE_WORKER_ADMISSION_CAP") or os.environ.get(
        "LIVEHOUSE_WORKER_CAPACITY"
    )
    if cap_env is not None and str(cap_env).strip() != "":
        try:
            slots = min(slots, max(1, int(cap_env)))
        except ValueError:
            pass

    pool_caps = _parse_executor_pool_caps(os.environ.get("LIVEHOUSE_WORKER_POOL_CAPS"))
    if wt in pool_caps and pool_caps[wt] > 0:
        slots = min(slots, pool_caps[wt])

    inf_raw = (
        os.environ.get("LIVEHOUSE_WORKER_INFERENCE_PROVIDER_SLOTS")
        or os.environ.get("LIVEHOUSE_MODEL_MAX_CONCURRENT_REQUESTS")
    )
    if inf_raw is not None and str(inf_raw).strip() != "":
        bind_general = os.environ.get("LIVEHOUSE_WORKER_BIND_INFERENCE_SLOTS_TO_GENERAL", "1").strip().lower() not in {
            "0",
            "false",
            "no",
        }
        try:
            inf_slots = max(1, int(inf_raw))
        except ValueError:
            inf_slots = None
        if inf_slots is not None:
            if wt == EXECUTOR_INFERENCE:
                slots = min(slots, inf_slots)
            elif wt == EXECUTOR_GENERAL and bind_general:
                slots = min(slots, inf_slots)

    return max(1, slots)
