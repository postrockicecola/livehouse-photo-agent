"""Best-effort Celery broker inspect for infra dashboards."""
from __future__ import annotations

import os
from typing import Any


def _task_preview(task: dict[str, Any]) -> dict[str, Any]:
    name = str(task.get("name") or task.get("type") or "?")
    tid = task.get("id")
    args = task.get("args")
    args_preview: str | None = None
    if isinstance(args, (list, tuple)) and args:
        try:
            args_preview = repr(args[0])[:120]
        except Exception:
            args_preview = None
    return {"name": name, "id": tid, "args_preview": args_preview}


def celery_worker_inspect_snapshot(*, timeout: float = 1.0) -> dict[str, Any]:
    """
    Per-Celery-process broker view (``control.inspect``).

    Returns ``workers`` list keyed by Celery hostname (e.g. ``general@host``).
    """
    from celery import Celery

    broker = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    client = Celery("infra_celery_inspect", broker=broker, backend=backend)
    try:
        inspect = client.control.inspect(timeout=timeout)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}
        stats = inspect.stats() or {}
        hostnames = sorted(set(active.keys()) | set(reserved.keys()) | set(scheduled.keys()) | set(stats.keys()))
        workers: list[dict[str, Any]] = []
        for hostname in hostnames:
            act = [t for t in (active.get(hostname) or []) if isinstance(t, dict)]
            res = [t for t in (reserved.get(hostname) or []) if isinstance(t, dict)]
            sch = [t for t in (scheduled.get(hostname) or []) if isinstance(t, dict)]
            st = stats.get(hostname) if isinstance(stats.get(hostname), dict) else {}
            pool = st.get("pool") if isinstance(st.get("pool"), dict) else {}
            workers.append(
                {
                    "celery_hostname": hostname,
                    "broker_online": True,
                    "active_count": len(act),
                    "reserved_count": len(res),
                    "scheduled_count": len(sch),
                    "active_tasks": [_task_preview(t) for t in act[:20]],
                    "reserved_tasks": [_task_preview(t) for t in res[:10]],
                    "pool_implementation": pool.get("implementation"),
                    "pool_max_concurrency": pool.get("max-concurrency"),
                }
            )
        return {
            "celery_unavailable": False,
            "worker_count": len(workers),
            "workers": workers,
            "error": None,
        }
    except Exception as exc:
        return {
            "celery_unavailable": True,
            "worker_count": 0,
            "workers": [],
            "error": str(exc),
        }


def celery_hostname_matches_worker_row(celery_hostname: str, worker_name: str) -> bool:
    """Match broker hostname (``general@host``) to SSOT ``brain@…`` worker_name."""
    ch = str(celery_hostname or "").strip().lower()
    wn = str(worker_name or "").strip().lower()
    if not ch or not wn:
        return False
    if wn == f"brain@{ch}":
        return True
    if wn.endswith("@" + ch):
        return True
    ch_host = ch.rsplit("@", 1)[-1]
    wn_host = wn.rsplit("@", 1)[-1]
    if ch_host and wn_host and ch_host == wn_host and wn.startswith("brain@"):
        return True
    return False


def enrich_worker_rows_with_broker(
    worker_rows: list[dict[str, Any]],
    broker_snapshot: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Attach ``celery_broker`` to each SSOT row; return ``(enriched_rows, unmatched_broker_workers)``.
    """
    snap = broker_snapshot if broker_snapshot is not None else celery_worker_inspect_snapshot()
    broker_workers = list(snap.get("workers") or [])
    matched_broker: set[str] = set()
    enriched: list[dict[str, Any]] = []

    for row in worker_rows:
        out = dict(row)
        wname = str(row.get("worker_name") or "")
        hit: dict[str, Any] | None = None
        for bw in broker_workers:
            ch = str(bw.get("celery_hostname") or "")
            if celery_hostname_matches_worker_row(ch, wname):
                hit = bw
                matched_broker.add(ch)
                break
        if hit is not None:
            out["celery_broker"] = {
                "online": True,
                "celery_hostname": hit.get("celery_hostname"),
                "active_count": hit.get("active_count", 0),
                "reserved_count": hit.get("reserved_count", 0),
                "scheduled_count": hit.get("scheduled_count", 0),
                "active_tasks": hit.get("active_tasks") or [],
                "pool_max_concurrency": hit.get("pool_max_concurrency"),
            }
        else:
            out["celery_broker"] = {"online": False}
        enriched.append(out)

    unmatched = [bw for bw in broker_workers if str(bw.get("celery_hostname") or "") not in matched_broker]
    return enriched, unmatched
