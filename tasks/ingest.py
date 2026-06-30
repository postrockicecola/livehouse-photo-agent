"""Ingest-related Celery tasks (seed jobs, dispatch executor by id).

**Recommended main path (dispatch leg):** Go ingest / ``POST /api/ingest/check_new_images``
→ :func:`process_brain_ingested` → ``seed_analyze_session_jobs`` →
``send_task("tasks.run_job", [job_id])`` only (runnable list is ``jobs``-only).

Does **not** run the pipeline in-process; execution continues in ``tasks.run_job``.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from celery.utils.log import get_task_logger

from celery_app import celery_app
from utils.logging_context import make_log_extra, new_trace_id

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.process_brain_ingested", bind=True)
def process_brain_ingested(
    self, config_path: str | None = None,
) -> Dict[str, Any]:
    """
    Ingest webhook path: seed ``ANALYZE_SESSION`` jobs using ``photos`` only as *candidate discovery*
    (sessions with files awaiting analysis outcome), then dispatch ``tasks.run_job`` by ``job_id`` only.

    Runnable selection is ``jobs``-only (:func:`~utils.luma_brain.list_runnable_analyze_jobs_for_ingested_sessions`);
    Celery / task return payload are not authoritative for lifecycle — SSOT is ``jobs`` + ``job_events``.
    """
    from services.scheduler import DispatchPolicy, plan_dispatch
    from utils.luma_brain import (
        brain_connect,
        dispatch_scope_from_env,
        get_jobs_dispatch_metadata,
        list_runnable_analyze_jobs_for_ingested_sessions,
        patch_job_payload,
        seed_analyze_session_jobs,
    )

    if not config_path:
        config_path = os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")

    trace_id = new_trace_id("brain_ingest")
    conn = brain_connect()
    try:
        scope_ns, scope_pk = dispatch_scope_from_env()
        seed_meta = seed_analyze_session_jobs(
            conn, job_type="ANALYZE_SESSION", limit=200, trace_id=trace_id
        )
        job_ids = list_runnable_analyze_jobs_for_ingested_sessions(
            conn,
            job_type="ANALYZE_SESSION",
            limit=500,
            namespace=scope_ns,
            project_key=scope_pk,
        )
        if not job_ids:
            return {
                "ok": True,
                "dispatched": 0,
                "job_ids": [],
                "seed": seed_meta,
                "message": "no runnable ANALYZE_SESSION jobs in jobs queue (after seed)",
                "dispatch_scope_env": {"namespace": scope_ns, "project_key": scope_pk},
            }
        rows = get_jobs_dispatch_metadata(conn, job_ids)
        plan = plan_dispatch(conn, rows, policy=DispatchPolicy.from_env())
        to_run = plan.selected_job_ids
        for jid in to_run:
            patch_job_payload(conn, job_id=jid, patch={"config_path": config_path})
    finally:
        conn.close()

    run_task_ids: list[str] = []
    for jid in to_run:
        run_task_ids.append(celery_app.send_task("tasks.run_job", args=[jid]).id)
    logger.info(
        "brain ingest: dispatch decision",
        extra=make_log_extra(
            trace_id=trace_id,
            status="DISPATCHED" if to_run else "SKIPPED",
            job_type="ANALYZE_SESSION",
            dispatched=len(to_run),
            candidates=len(job_ids),
            event="dispatch_decision",
            dispatch_plan=plan.to_log_dict(),
            namespace=scope_ns,
            project_key=scope_pk,
        ),
    )
    return {
        "ok": True,
        "dispatched": len(to_run),
        "job_ids": to_run,
        "candidates": len(job_ids),
        "run_task_ids": run_task_ids,
        "seed": seed_meta,
        "config_path": config_path,
        "trace_id": trace_id,
        "dispatch_plan": plan.to_log_dict(),
        "dispatch_scope_env": {"namespace": scope_ns, "project_key": scope_pk},
    }
