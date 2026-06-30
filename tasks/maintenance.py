"""Scheduled / operational maintenance Celery tasks."""
from __future__ import annotations

from typing import Any, Dict

from celery.utils.log import get_task_logger

from celery_app import celery_app
from utils.logging_context import make_log_extra

logger = get_task_logger(__name__)


def _dispatch_after_requeue(conn: Any, job_ids: list[int]) -> Dict[str, Any] | None:
    """Enqueue ``run_job`` for ids just moved back to ``QUEUED`` (respects headroom / fair caps)."""
    if not job_ids:
        return None
    from services.job_dispatch import send_run_jobs_for_ids

    return send_run_jobs_for_ids(conn, job_ids)


@celery_app.task(name="tasks.requeue_stuck_jobs", bind=True)
def requeue_stuck_jobs(
    self,
    stale_after_seconds: int = 15 * 60,
    worker_stale_after_seconds: int = 5 * 60,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Requeue stuck active jobs to keep queue progressing.

    Calls :func:`utils.luma_brain.requeue_stuck_jobs` with dual timeouts (job claim age vs worker
    heartbeat staleness); see that docstring for false-positive caveats. Also runs
    :func:`utils.luma_brain.reconcile_exhausted_retryable_to_dead_letter` and may dispatch Celery
    ``run_job`` for ids returned to ``QUEUED``.
    """
    from utils.luma_brain import (
        brain_connect,
        reconcile_exhausted_retryable_to_dead_letter,
        requeue_stuck_jobs as requeue_stuck_jobs_db,
    )

    conn = brain_connect()
    try:
        job_ids = requeue_stuck_jobs_db(
            conn,
            stale_after_seconds=stale_after_seconds,
            worker_stale_after_seconds=worker_stale_after_seconds,
            limit=limit,
            reason="requeued by scheduled stuck scan",
        )
        promoted = reconcile_exhausted_retryable_to_dead_letter(conn, limit=max(500, limit * 2))
        if promoted:
            logger.info(
                "exhausted retryable jobs dead-lettered",
                extra=make_log_extra(
                    status="DEAD_LETTERED",
                    promoted=len(promoted),
                    job_ids_sample=promoted[:20],
                ),
            )
        dispatch_meta = _dispatch_after_requeue(conn, job_ids)
        if job_ids:
            logger.info(
                "stuck jobs requeued",
                extra=make_log_extra(
                    status="REQUEUED",
                    requeued=len(job_ids),
                    job_ids_sample=job_ids[:20],
                ),
            )
        if dispatch_meta and dispatch_meta.get("dispatched"):
            logger.info(
                "requeued jobs: celery dispatch",
                extra=make_log_extra(
                    event="post_requeue_dispatch",
                    dispatched=dispatch_meta.get("dispatched"),
                    job_ids_sample=(dispatch_meta.get("dispatched_job_ids") or [])[:20],
                ),
            )
        return {
            "ok": True,
            "stale_after_seconds": stale_after_seconds,
            "worker_stale_after_seconds": worker_stale_after_seconds,
            "limit": limit,
            "requeued_jobs": len(job_ids),
            "job_ids": job_ids,
            "post_requeue_dispatch": dispatch_meta,
        }
    finally:
        conn.close()


@celery_app.task(name="tasks.scan_and_requeue_stuck_jobs", bind=True)
def scan_and_requeue_stuck_jobs(
    self,
    stale_after_seconds: int = 15 * 60,
    worker_stale_after_seconds: int = 5 * 60,
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Periodic maintenance task:
    scan active jobs and requeue only those whose owner worker heartbeat is stale.
    """
    from utils.luma_brain import (
        brain_connect,
        reconcile_exhausted_retryable_to_dead_letter,
        requeue_stuck_jobs as requeue_stuck_jobs_db,
    )

    job_ids: list[int] = []
    dispatch_meta: Dict[str, Any] | None = None
    conn = brain_connect()
    try:
        job_ids = requeue_stuck_jobs_db(
            conn,
            stale_after_seconds=stale_after_seconds,
            worker_stale_after_seconds=worker_stale_after_seconds,
            limit=limit,
            reason="requeued by periodic scan task",
        )
        promoted = reconcile_exhausted_retryable_to_dead_letter(conn, limit=max(500, limit * 2))
        if promoted:
            logger.info(
                "exhausted retryable jobs dead-lettered (periodic)",
                extra=make_log_extra(
                    status="DEAD_LETTERED",
                    promoted=len(promoted),
                    job_ids_sample=promoted[:20],
                ),
            )
        if job_ids:
            dispatch_meta = _dispatch_after_requeue(conn, job_ids)
            logger.info(
                "stuck jobs requeued (periodic)",
                extra=make_log_extra(
                    status="REQUEUED",
                    requeued=len(job_ids),
                    job_ids_sample=job_ids[:20],
                ),
            )
        if dispatch_meta and dispatch_meta.get("dispatched"):
            logger.info(
                "periodic requeue: celery dispatch",
                extra=make_log_extra(
                    event="post_requeue_dispatch",
                    dispatched=dispatch_meta.get("dispatched"),
                    job_ids_sample=(dispatch_meta.get("dispatched_job_ids") or [])[:20],
                ),
            )
    finally:
        conn.close()
    return {
        "ok": True,
        "scan": "stuck_jobs",
        "stale_after_seconds": stale_after_seconds,
        "worker_stale_after_seconds": worker_stale_after_seconds,
        "limit": limit,
        "requeued_jobs": len(job_ids),
        "job_ids": job_ids,
        "post_requeue_dispatch": dispatch_meta,
    }


@celery_app.task(name="tasks.dispatch_runnable_jobs", bind=True)
def dispatch_runnable_jobs(self, candidate_limit: int = 500) -> Dict[str, Any]:
    """
    Periodic dispatch: enqueue ``tasks.run_job`` for runnable SSOT rows (``QUEUED`` / ``FAILED_RETRYABLE``).

    Drains work that ingest never picked up and completes recovery after ``requeue_stuck_jobs`` when
    headroom only allows a partial batch per tick. Headroom reflects ONLINE worker capacity minus live
    pipeline jobs — see ``cluster_headroom_for_dispatch``.
    """
    from utils.luma_brain import brain_connect

    from services.job_dispatch import dispatch_runnable_jobs_round

    conn = brain_connect()
    try:
        out = dispatch_runnable_jobs_round(conn, candidate_limit=candidate_limit)
        if out.get("dispatched"):
            logger.info(
                "dispatch_runnable_jobs",
                extra=make_log_extra(
                    event="dispatch_runnable_jobs",
                    listed=out.get("runnable_listed"),
                    dispatched=out.get("dispatched"),
                    job_ids_sample=(out.get("dispatched_job_ids") or [])[:20],
                ),
            )
        return out
    finally:
        conn.close()
