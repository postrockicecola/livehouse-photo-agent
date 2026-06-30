"""
Celery ``tasks.run_job`` dispatch: respect :class:`services.scheduler.DispatchPolicy` / headroom.

Runnable SSOT rows: statuses in :data:`utils.luma_brain.JOB_STATUSES_RUNNABLE`, plus dependency satisfaction
and ``attempt < max_attempts`` (see :func:`utils.luma_brain.list_runnable_job_ids_for_dispatch`).

Used after stuck-job requeue and by periodic ``tasks.dispatch_runnable_jobs`` so ``QUEUED`` work
is not stranded when only the maintenance beat runs (no ingest).
"""
from __future__ import annotations

from typing import Any

from celery_app import celery_app
from services.scheduler import DispatchPolicy, plan_dispatch_for_job_ids
from utils.luma_brain import list_runnable_job_ids_for_dispatch, dispatch_scope_from_env


def send_run_jobs_for_ids(
    conn: Any,
    job_ids: list[int],
    *,
    policy: DispatchPolicy | None = None,
) -> dict[str, Any]:
    """
    Enqueue ``run_job`` Celery tasks for a subset of ``job_ids`` chosen by :func:`plan_dispatch_for_job_ids`.
    """
    if not job_ids:
        return {
            "ok": True,
            "candidates": 0,
            "dispatched": 0,
            "dispatched_job_ids": [],
            "celery_task_ids": [],
            "plan": None,
        }
    p = policy or DispatchPolicy.from_env()
    plan = plan_dispatch_for_job_ids(conn, job_ids, policy=p)
    celery_task_ids: list[str] = []
    dispatched_job_ids: list[int] = []
    for jid in plan.selected_job_ids:
        celery_task_ids.append(celery_app.send_task("tasks.run_job", args=[int(jid)]).id)
        dispatched_job_ids.append(int(jid))
    return {
        "ok": True,
        "candidates": len(job_ids),
        "dispatched": len(dispatched_job_ids),
        "dispatched_job_ids": dispatched_job_ids,
        "celery_task_ids": celery_task_ids,
        "plan": plan.to_log_dict(),
    }


def dispatch_runnable_jobs_round(
    conn: Any,
    *,
    candidate_limit: int = 500,
    policy: DispatchPolicy | None = None,
) -> dict[str, Any]:
    """One dispatch round: list runnable jobs from SSOT, plan, ``send_task`` for the selected batch."""
    scope_ns, scope_pk = dispatch_scope_from_env()
    job_ids = list_runnable_job_ids_for_dispatch(
        conn,
        limit=candidate_limit,
        namespace=scope_ns,
        project_key=scope_pk,
    )
    out = send_run_jobs_for_ids(conn, job_ids, policy=policy)
    out["runnable_listed"] = len(job_ids)
    out["dispatch_scope_env"] = {"namespace": scope_ns, "project_key": scope_pk}
    return out
