"""Additional Celery tasks (deprecated analysis shim, demos, job seeding).

**Not the primary pipeline package:** execution SSOT lives in ``tasks/run_job.py`` + ``tasks/ingest.py``.
This file keeps broker-compatible shims (``run_image_analysis``) and optional/demo tasks.
"""
from __future__ import annotations

from typing import Any, Dict

from celery.utils.log import get_task_logger

from celery_app import celery_app
from infra.worker_manager import WorkerManager
from services.job_errors import classify_exception
from services.job_lifecycle import JobLifecycle
from utils.logging_context import make_log_extra, new_trace_id

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.run_image_analysis", bind=True)
def run_image_analysis(
    self,
    config_path: str = "configs/livehouse.yaml",
    source_dir: str | None = None,
    max_workers: int | None = None,
    enable_checkpoint: bool = True,
) -> Dict[str, Any]:
    """
    **Deprecated** compatibility shim.

    Prefer: persist via ``create_analyze_path_job`` (or API), then dispatch with
    ``celery_app.send_task("tasks.run_job", args=[job_id])`` so callers do not
    import task objects.

    This task only chains: (1) create job row (SSOT), (2) enqueue executor by name.
    """
    from utils.luma_brain import brain_connect, create_analyze_path_job

    if not source_dir:
        raise ValueError("run_image_analysis requires source_dir")
    if max_workers is not None:
        try:
            max_workers = int(max_workers)
        except (TypeError, ValueError):
            max_workers = None
    trace_id = new_trace_id("analyze_path")
    conn = brain_connect()
    try:
        job_id = create_analyze_path_job(
            conn,
            source_dir=source_dir,
            config_path=config_path,
            max_workers=max_workers,
            enable_checkpoint=enable_checkpoint,
            trace_id=trace_id,
        )
    finally:
        conn.close()
    async_result = celery_app.send_task("tasks.run_job", args=[job_id])
    logger.warning(
        "deprecated tasks.run_image_analysis invoked; prefer create_job + send_task(tasks.run_job)",
        extra=make_log_extra(
            trace_id=trace_id,
            job_id=job_id,
            job_type="ANALYZE_PATH",
            status="QUEUED",
        ),
    )
    return {
        "ok": True,
        "job_id": job_id,
        "run_task_id": async_result.id,
        "trace_id": trace_id,
    }


def _load_luma_callable():
    """
    Resolve user's requested luma_professional_workflow entrypoint.
    Priority:
      1) module `luma_professional_workflow.py` with function `luma_professional_workflow`
      2) fallback to `luma_render2.luma_advanced_inpainting_workflow`
    """
    try:
        from luma_professional_workflow import luma_professional_workflow  # type: ignore

        return luma_professional_workflow, "luma_professional_workflow.luma_professional_workflow"
    except Exception:
        pass

    try:
        from luma_render2 import luma_advanced_inpainting_workflow

        return luma_advanced_inpainting_workflow, "luma_render2.luma_advanced_inpainting_workflow"
    except Exception:
        return None, None


@celery_app.task(name="tasks.run_luma_professional_workflow", bind=True)
def run_luma_professional_workflow(self, raw_path: str, out_path: str) -> Dict[str, Any]:
    """
    Execute luma professional workflow as async Celery task.
    """
    fn, source_name = _load_luma_callable()
    if fn is None:
        raise RuntimeError(
            "No luma workflow callable found. Please add `luma_professional_workflow.py` "
            "with function `luma_professional_workflow(raw_path, out_path)`."
        )

    logger.info("Running luma workflow via %s", source_name)
    fn(raw_path, out_path)
    return {
        "ok": True,
        "workflow": source_name,
        "raw_path": raw_path,
        "out_path": out_path,
    }


@celery_app.task(name="tasks.run_brain_job_once", bind=True)
def run_brain_job_once(self, worker_id: int | None = None, job_type: str | None = None) -> Dict[str, Any]:
    """
    Minimal AI-infra demo task:
    - claim one job from luma_brain.jobs
    - mark PREPROCESSING -> INFERENCING -> SUCCEEDED
    - write lifecycle events via utils.luma_brain helpers

    This does NOT replace current photo pipeline tasks; it's a small compatible entrypoint.
    """
    from utils.luma_brain import (
        brain_connect,
        claim_jobs,
        count_active_jobs_for_worker,
        requeue_stuck_jobs,
    )

    conn = brain_connect()
    try:
        wm = WorkerManager.for_celery_task(conn, task_self=self, explicit_worker_id=worker_id)
        if worker_id is not None:
            wm.heartbeat(status="ONLINE")
        else:
            wm.get_worker_id()

        wid = wm.get_worker_id()
        lifecycle = JobLifecycle(conn)

        requeued = requeue_stuck_jobs(
            conn,
            stale_after_seconds=15 * 60,
            worker_stale_after_seconds=5 * 60,
            limit=50,
            reason="requeued by run_brain_job_once stuck-scan",
        )
        if requeued:
            logger.info(
                "stuck jobs requeued",
                extra=make_log_extra(
                    worker_id=wid,
                    status="REQUEUED",
                    requeued=len(requeued),
                    job_ids_sample=requeued[:20],
                ),
            )
        wm.heartbeat(inflight=count_active_jobs_for_worker(conn, wid), status="ONLINE")

        claimed = claim_jobs(conn, worker_id=wid, job_type=job_type, limit=1)
        if not claimed:
            return {
                "ok": True,
                "claimed": False,
                "worker_id": wid,
                "requeued_stuck_jobs": requeued,
                "message": "no runnable jobs",
            }

        job = claimed[0]
        job_id = int(job["id"])
        wm.heartbeat(inflight=count_active_jobs_for_worker(conn, wid), status="ONLINE")
        try:
            lifecycle.update_status(
                job_id,
                to_status="PREPROCESSING",
                message="brain worker preprocessing",
            )
            lifecycle.update_status(
                job_id,
                to_status="INFERENCING",
                message="brain worker inferencing",
            )
            lifecycle.succeed(
                job_id,
                preprocess_ms=50,
                inference_ms=120,
                postprocess_ms=30,
                total_latency_ms=200,
                payload={"worker_id": wid, "mode": "demo_once"},
            )
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, wid), status="ONLINE")
            return {
                "ok": True,
                "claimed": True,
                "worker_id": wid,
                "job_id": job_id,
                "status": "SUCCEEDED",
                "requeued_stuck_jobs": requeued,
            }
        except Exception as exc:
            error_name = type(exc).__name__
            error_message = str(exc)[:500]
            if classify_exception(exc) == "permanent":
                lifecycle.fail_permanent(
                    job_id,
                    error_code=error_name,
                    error_message=error_message,
                    payload={"worker_id": wid, "mode": "demo_once"},
                )
            else:
                lifecycle.fail_retryable(
                    job_id,
                    error_code=error_name,
                    error_message=error_message,
                    payload={"worker_id": wid, "mode": "demo_once"},
                )
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, wid), status="ERROR")
            raise
    finally:
        conn.close()


@celery_app.task(name="tasks.create_analysis_jobs", bind=True)
def create_analysis_jobs(
    self,
    job_type: str = "ANALYZE_SESSION",
    limit: int = 200,
) -> Dict[str, Any]:
    """
    Create ``ANALYZE_SESSION`` rows for sessions discovered via ``photos.status = INGESTED`` (outcome pending).

    Execution remains ``jobs``-centric; ``INGESTED`` here only picks which sessions get a new job row.
    Implementation: :func:`utils.luma_brain.seed_analyze_session_jobs`.
    """
    from utils.luma_brain import brain_connect, seed_analyze_session_jobs

    conn = brain_connect()
    try:
        trace_id = new_trace_id("create_jobs")
        out = seed_analyze_session_jobs(
            conn, job_type=job_type, limit=limit, trace_id=trace_id
        )
        for jid in out["created_job_ids"]:
            logger.info(
                "job created",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=jid,
                    job_type=job_type,
                    status="QUEUED",
                ),
            )
        return out
    finally:
        conn.close()


@celery_app.task(name="tasks.start_staged_session_pipeline", bind=True)
def start_staged_session_pipeline(
    self,
    session_id: int,
    config_path: str = "configs/livehouse.yaml",
) -> Dict[str, Any]:
    """
    Opt-in: create linear ``PIPELINE_STAGE`` jobs for a session and dispatch the first stage.
    Does **not** run the monolithic ``AestheticPipeline``; use ``seed_analyze_session_jobs`` for that.
    """
    from utils.luma_brain import brain_connect, create_linear_staged_session_jobs

    trace_id = new_trace_id("staged_pipeline")
    conn = brain_connect()
    try:
        out = create_linear_staged_session_jobs(
            conn,
            session_id=int(session_id),
            trace_id=trace_id,
            config_path=config_path,
        )
    finally:
        conn.close()
    first = (out.get("stage_job_ids") or [None])[0]
    if first is not None:
        celery_app.send_task("tasks.run_job", args=[int(first)])
    return {**out, "trace_id": trace_id, "dispatched_job_id": first}
