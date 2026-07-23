"""
Orchestrates ``tasks.run_job``: worker handshake, claim, path resolution, pipeline, finalize.

**Recommended main path (execution core):** ``ingest → seed jobs → tasks.run_job(job_id) → *JobExecutor* →
:class:`~services.processor.pipeline_stage_runner.PipelineStageRunner` → inference →
artifacts (:mod:`services.job_artifacts`, ``analysis_results.json``, ``job_events``).

Celery is a stateless trigger; SSOT is ``jobs`` + ``job_events``. Do not add parallel executor
entrypoints without updating this contract.

**Compatibility / legacy (same runner, different entry):** ``AestheticPipeline.run``,
``run_pipeline.py``, Go mode A ``pipeline-cmd`` subprocess — bypass or weaken job visibility.

Photo ``ANALYZED`` updates (:func:`~services.processor.pipeline_stage_runner.finalize_session_if_needed`)
are ingest/outcome ledger only — not the execution state machine.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from celery.utils.log import get_task_logger

from infra.worker_manager import WorkerManager, long_task_heartbeat
from services.gallery_film_prewarm import enqueue_gallery_cinestill_prewarm
from services.job_artifacts import build_success_artifact_event_payload
from services.job_errors import classify_exception
from services.job_lifecycle import JobLifecycle
from services.job_payload import parse_job_payload
from services.pipeline_stages import STAGE_JOB_TYPE
from services.processor.pipeline_stage_runner import (
    PipelineStageRunner,
    finalize_session_if_needed,
)
from utils.logging_context import make_log_extra, new_trace_id
from utils.luma_brain import (
    ClaimFenceError,
    brain_connect,
    count_active_jobs_for_worker,
    get_job,
)

logger = get_task_logger(__name__)


class JobExecutor:
    """Run one job by id using an optional Celery task instance for hostname context."""

    def __init__(self, task_self: Any | None = None) -> None:
        self._task_self = task_self

    def run(self, job_id: int) -> dict[str, Any]:
        conn = brain_connect()
        try:
            wm = WorkerManager.for_celery_task(conn, task_self=self._task_self)
            worker_id = wm.get_worker_id()
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, worker_id), status="ONLINE")

            lifecycle = JobLifecycle(conn)
            claimed, claim_err = lifecycle.claim(job_id, worker_id)
            if not claimed:
                if claim_err and str(claim_err).startswith("worker_admission:"):
                    return self._admission_denied(conn, job_id, worker_id, str(claim_err))
                return self._claim_skipped(conn, job_id, worker_id)

            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, worker_id), status="ONLINE")
            return self._run_claimed(
                conn,
                wm=wm,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                claimed=claimed,
            )
        finally:
            conn.close()

    def _admission_denied(
        self,
        conn: Any,
        job_id: int,
        worker_id: int,
        claim_err: str,
    ) -> dict[str, Any]:
        current = get_job(conn, job_id=job_id)
        trace_for_log = (current or {}).get("trace_id") if current else None
        logger.info(
            "job claim skipped (worker runtime admission)",
            extra=make_log_extra(
                trace_id=trace_for_log,
                job_id=job_id,
                worker_id=worker_id,
                claim_err=claim_err,
            ),
        )
        return {
            "ok": False,
            "claimed": False,
            "job_id": job_id,
            "worker_id": worker_id,
            "message": "worker not accepting new work (pause/drain/capacity)",
            "admission": claim_err,
        }

    def _claim_skipped(self, conn: Any, job_id: int, worker_id: int) -> dict[str, Any]:
        current = get_job(conn, job_id=job_id)
        cur_status = (current or {}).get("status")
        trace_for_log = (current or {}).get("trace_id") if current else None
        logger.info(
            "job claim skipped (not runnable or missing)",
            extra=make_log_extra(
                trace_id=trace_for_log,
                job_id=job_id,
                worker_id=worker_id,
                job_type=(current or {}).get("job_type") if current else None,
                status=cur_status,
            ),
        )
        return {
            "ok": False,
            "claimed": False,
            "job_id": job_id,
            "worker_id": worker_id,
            "message": "job not runnable or missing",
            "current_status": cur_status,
        }

    def _run_claimed(
        self,
        conn: Any,
        *,
        wm: WorkerManager,
        lifecycle: JobLifecycle,
        job_id: int,
        worker_id: int,
        claimed: dict[str, Any],
    ) -> dict[str, Any]:
        run_started_ms = int(time.time() * 1000)
        job_type = str(claimed.get("job_type") or "")
        trace_for_payload = claimed.get("trace_id")
        trace_id_str = str(trace_for_payload) if trace_for_payload not in (None, "") else None
        payload = parse_job_payload(claimed, job_id=job_id, trace_id=trace_id_str)
        trace_id = trace_id_str or new_trace_id("job")
        scope_ns = claimed.get("namespace")
        scope_pk = claimed.get("project_key")
        config_path = str(
            payload.get("config_path")
            or os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")
        )
        max_workers = payload.get("max_workers")
        if max_workers is not None:
            try:
                max_workers = int(max_workers)
            except (TypeError, ValueError):
                max_workers = None
        session_id = claimed.get("session_id")
        provider = str(claimed.get("provider") or "ollama")
        model_name = str(claimed.get("model_name") or "")

        if job_type == STAGE_JOB_TYPE:
            return self._run_pipeline_stage_job(
                conn,
                wm=wm,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                claimed=claimed,
                run_started_ms=run_started_ms,
                trace_id=trace_id,
                provider=provider,
                model_name=model_name,
                payload=payload,
            )

        if job_type in ("CURATE_PATH", "CURATE_SESSION"):
            return self._run_curation_job(
                conn,
                wm=wm,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                claimed=claimed,
                run_started_ms=run_started_ms,
                trace_id=trace_id,
                provider=provider,
                model_name=model_name,
                payload=payload,
                session_id=session_id,
                config_path=config_path,
            )

        source_dir, pipeline_session_id = self._resolve_source_dir(
            conn, job_id=job_id, job_type=job_type, session_id=session_id, payload=payload
        )
        enable_checkpoint = bool(payload.get("enable_checkpoint", True))
        force_full_rerun = bool(payload.get("force_full_rerun", False))
        if force_full_rerun:
            enable_checkpoint = False

        try:
            lifecycle.start_pipeline_preprocessing(
                job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                job_type=job_type,
            )
            lifecycle.start_pipeline_inferencing(
                job_id,
                source_dir=source_dir,
                trace_id=trace_id,
                job_type=job_type,
            )
            logger.info(
                "job start",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    worker_id=worker_id,
                    provider=provider,
                    model=model_name,
                    status="INFERENCING",
                    job_type=job_type,
                    namespace=scope_ns,
                    project_key=scope_pk,
                ),
            )
            runner = PipelineStageRunner(
                config_path=config_path,
                source_dir=source_dir,
                trace_id=trace_id,
                job_id=job_id,
                worker_id=worker_id,
                session_id=pipeline_session_id,
            )
            with long_task_heartbeat(wm, status="ONLINE"):
                if force_full_rerun:
                    runner.reset_for_full_rerun()
                runner.run_prepare_input()
                runner.run_stage1_filter(
                    max_workers=max_workers, enable_checkpoint=enable_checkpoint
                )
                runner.run_stage2_fast_score(max_workers=max_workers)
                runner.run_stage3_vlm(max_workers=max_workers, conn=None)
                write_out = runner.run_write_artifact()

            pipeline_result = {"artifact_paths": write_out.get("artifact_paths") or {}}

            # Outcome ledger only (jobs already progressed via lifecycle.* above).
            if job_type == "ANALYZE_SESSION" and pipeline_session_id is not None:
                finalize_session_if_needed(conn, pipeline_session_id)

            total_latency_ms = max(0, int(time.time() * 1000) - run_started_ms)
            generated_at = int(time.time())
            ap = (
                (pipeline_result or {}).get("artifact_paths")
                if isinstance(pipeline_result, dict)
                else None
            ) or {}
            succeed_payload = build_success_artifact_event_payload(
                base={
                    "worker_id": worker_id,
                    "session_id": session_id,
                    "source_dir": source_dir,
                    "trace_id": trace_id,
                    "job_type": job_type,
                },
                analysis_results_path=ap.get("analysis_results"),
                preview_html_path=ap.get("preview_html"),
                folder_galleries=list(ap.get("folder_galleries") or []),
                launch_scripts=list(ap.get("launch_scripts") or []),
                generated_at=generated_at,
            )
            lifecycle.succeed(
                job_id,
                total_latency_ms=total_latency_ms,
                payload=succeed_payload,
                **JobLifecycle.claim_fence_kwargs(claimed),
            )
            prewarm_task_id = enqueue_gallery_cinestill_prewarm(
                force=True,
                analysis_results_path=ap.get("analysis_results"),
                source_dir=source_dir,
            )
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, worker_id), status="ONLINE")
            logger.info(
                "job succeeded",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    worker_id=worker_id,
                    provider=provider,
                    model=model_name,
                    status="SUCCEEDED",
                    latency_ms=total_latency_ms,
                    job_type=job_type,
                    namespace=scope_ns,
                    project_key=scope_pk,
                ),
            )
            return {
                "ok": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "status": "SUCCEEDED",
                "session_id": session_id,
                "source_dir": source_dir,
                "total_latency_ms": total_latency_ms,
                "job_type": job_type,
                "primary_artifact": succeed_payload.get("primary_artifact"),
                "artifacts": succeed_payload.get("artifacts"),
                "gallery_film_prewarm_task_id": prewarm_task_id,
            }
        except ClaimFenceError as fence_exc:
            logger.warning(
                "job terminal write fenced (stale claim)",
                extra=make_log_extra(
                    job_id=job_id,
                    worker_id=worker_id,
                    trace_id=trace_id,
                    error=str(fence_exc),
                ),
            )
            return {
                "ok": False,
                "claimed": True,
                "fenced": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "message": str(fence_exc),
            }
        except Exception as exc:
            self._record_pipeline_failure(
                wm=wm,
                conn=conn,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                provider=provider,
                model_name=model_name,
                job_type=job_type,
                run_started_ms=run_started_ms,
                exc=exc,
                namespace=scope_ns,
                project_key=scope_pk,
                claimed=claimed,
            )
            raise

    def _run_pipeline_stage_job(
        self,
        conn: Any,
        *,
        wm: Any,
        lifecycle: JobLifecycle,
        job_id: int,
        worker_id: int,
        claimed: dict[str, Any],
        run_started_ms: int,
        trace_id: str,
        provider: str,
        model_name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """
        ``PIPELINE_STAGE``: per-stage lifecycle, timings, and real pipeline work via
        :class:`services.processor.pipeline_stage_runner.PipelineStageRunner`.
        """
        stage_name = str(claimed.get("stage_name") or "").strip()
        session_id = claimed.get("session_id")
        source_dir = str(payload.get("source_dir") or "").strip()
        if not source_dir:
            raise ValueError(f"PIPELINE_STAGE job {job_id}: missing payload.source_dir")
        src_path = Path(source_dir)
        if not src_path.is_dir():
            raise FileNotFoundError(
                f"PIPELINE_STAGE job {job_id}: source_dir is not a directory: {source_dir!r}"
            )
        config_path = str(
            payload.get("config_path")
            or os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")
        )
        max_workers = payload.get("max_workers")
        if max_workers is not None:
            try:
                max_workers = int(max_workers)
            except (TypeError, ValueError):
                max_workers = None
        try:
            t0 = time.perf_counter()
            lifecycle.start_pipeline_preprocessing(
                job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                job_type=STAGE_JOB_TYPE,
            )
            t1 = time.perf_counter()
            lifecycle.start_pipeline_inferencing(
                job_id,
                source_dir=source_dir,
                trace_id=trace_id,
                job_type=STAGE_JOB_TYPE,
            )
            t2 = time.perf_counter()
            runner = PipelineStageRunner(
                config_path=config_path,
                source_dir=source_dir,
                trace_id=trace_id,
                job_id=job_id,
                worker_id=worker_id,
                session_id=int(session_id) if session_id is not None else None,
            )

            stage_result: dict[str, Any] = {}
            inference_wall = 0
            artifact_paths: dict[str, Any] | None = None

            if stage_name == "PREPARE_INPUT":
                stage_result = runner.run_prepare_input()
            elif stage_name == "STAGE1_FILTER":
                enable_ckpt = bool(payload.get("enable_checkpoint", True))
                stage_result = runner.run_stage1_filter(
                    max_workers=max_workers, enable_checkpoint=enable_ckpt
                )
                inference_wall = int(stage_result.get("inference_wall_ms") or 0)
            elif stage_name == "STAGE2_FAST_SCORE":
                stage_result = runner.run_stage2_fast_score(max_workers=max_workers)
                inference_wall = int(stage_result.get("inference_wall_ms") or 0)
            elif stage_name == "STAGE3_VLM":
                stage_result = runner.run_stage3_vlm(max_workers=max_workers, conn=conn)
                inference_wall = int(stage_result.get("inference_wall_ms") or 0)
            elif stage_name == "WRITE_ARTIFACT":
                stage_result = runner.run_write_artifact()
                inference_wall = int(float(stage_result.get("writing_wall_seconds") or 0.0) * 1000.0)
                ap = stage_result.get("artifact_paths") or {}
                artifact_paths = ap if isinstance(ap, dict) else None
            elif stage_name == "FINALIZE":
                finalize_session_if_needed(conn, int(session_id) if session_id is not None else None)
                stage_result = {"session_id": session_id}
            else:
                raise ValueError(f"PIPELINE_STAGE job {job_id}: unsupported stage_name {stage_name!r}")

            t3 = time.perf_counter()
            lifecycle.update_status(
                job_id,
                to_status="POSTPROCESSING",
                message=f"stage {stage_name} postprocess complete",
                payload={
                    "stage_name": stage_name,
                    "trace_id": trace_id,
                    "stage_summary": stage_result,
                },
            )
            t4 = time.perf_counter()
            preprocess_ms = max(0, int((t1 - t0) * 1000))
            inference_ms = inference_wall if inference_wall > 0 else max(0, int((t3 - t2) * 1000))
            postprocess_ms = max(0, int((t4 - t3) * 1000))
            total_latency_ms = max(0, int(time.time() * 1000) - run_started_ms)
            generated_at = int(time.time())
            exec_mode = payload.get("execution_mode") or "staged_pipeline"
            succeed_base: dict[str, Any] = {
                "worker_id": worker_id,
                "session_id": session_id,
                "source_dir": source_dir,
                "trace_id": trace_id,
                "job_type": STAGE_JOB_TYPE,
                "stage_name": stage_name,
                "execution_mode": exec_mode,
                "config_path": config_path,
                "stage_output": stage_result,
            }

            ap = artifact_paths if artifact_paths else None
            if artifact_paths:
                succeed_payload = build_success_artifact_event_payload(
                    base=dict(succeed_base),
                    analysis_results_path=ap.get("analysis_results") if ap else None,
                    preview_html_path=ap.get("preview_html") if ap else None,
                    folder_galleries=list(ap.get("folder_galleries") or []),
                    launch_scripts=list(ap.get("launch_scripts") or []),
                    generated_at=generated_at,
                )
            elif stage_name == "WRITE_ARTIFACT":
                succeed_payload = {**succeed_base, "artifacts": [], "warn": "artifact_paths_missing"}
            else:
                succeed_payload = succeed_base

            lifecycle.succeed(
                job_id,
                total_latency_ms=total_latency_ms,
                preprocess_ms=preprocess_ms,
                inference_ms=inference_ms,
                postprocess_ms=postprocess_ms,
                payload=succeed_payload,
                **JobLifecycle.claim_fence_kwargs(claimed),
            )
            prewarm_task_id = None
            if stage_name == "WRITE_ARTIFACT" and ap:
                prewarm_task_id = enqueue_gallery_cinestill_prewarm(
                    force=True,
                    analysis_results_path=ap.get("analysis_results"),
                    source_dir=source_dir,
                )
            self._dispatch_next_pipeline_stage(conn, job_id)
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, worker_id), status="ONLINE")
            logger.info(
                "pipeline stage succeeded",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    worker_id=worker_id,
                    provider=provider,
                    model=model_name,
                    status="SUCCEEDED",
                    latency_ms=total_latency_ms,
                    job_type=STAGE_JOB_TYPE,
                    namespace=claimed.get("namespace"),
                    project_key=claimed.get("project_key"),
                ),
            )
            return {
                "ok": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "status": "SUCCEEDED",
                "session_id": session_id,
                "source_dir": source_dir,
                "total_latency_ms": total_latency_ms,
                "job_type": STAGE_JOB_TYPE,
                "stage_name": stage_name,
                "gallery_film_prewarm_task_id": prewarm_task_id,
                "stage_output_summary": stage_result,
            }
        except ClaimFenceError as fence_exc:
            logger.warning(
                "pipeline stage terminal write fenced (stale claim)",
                extra=make_log_extra(
                    job_id=job_id,
                    worker_id=worker_id,
                    trace_id=trace_id,
                    error=str(fence_exc),
                ),
            )
            return {
                "ok": False,
                "claimed": True,
                "fenced": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "message": str(fence_exc),
            }
        except Exception as exc:
            self._record_pipeline_failure(
                wm=wm,
                conn=conn,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                provider=provider,
                model_name=model_name,
                job_type=STAGE_JOB_TYPE,
                run_started_ms=run_started_ms,
                exc=exc,
                namespace=claimed.get("namespace"),
                project_key=claimed.get("project_key"),
                claimed=claimed,
            )
            raise

    def _run_curation_job(
        self,
        conn: Any,
        *,
        wm: Any,
        lifecycle: JobLifecycle,
        job_id: int,
        worker_id: int,
        claimed: dict[str, Any],
        run_started_ms: int,
        trace_id: str,
        provider: str,
        model_name: str,
        payload: dict[str, Any],
        session_id: Any,
        config_path: str,
    ) -> dict[str, Any]:
        """``CURATE_PATH`` / ``CURATE_SESSION``: run the agentic curation loop.

        Same lifecycle/observability contract as the pipeline executor — the agent's
        per-step decisions land in ``job_events`` (via the job runner's step hook) so
        the Infra Console timeline shows the loop, not just a single opaque job.
        """
        from services.agent.job_runner import run_curation_job

        job_type = str(claimed.get("job_type") or "")
        if job_type == "CURATE_SESSION":
            source_dir, pipeline_session_id = self._resolve_source_dir(
                conn, job_id=job_id, job_type="ANALYZE_SESSION", session_id=session_id, payload=payload
            )
        else:
            sd = str(payload.get("source_dir") or "").strip()
            if not sd:
                raise ValueError(f"CURATE_PATH job {job_id}: missing payload.source_dir")
            if not Path(sd).is_dir():
                raise FileNotFoundError(
                    f"CURATE_PATH job {job_id}: source_dir is not a directory: {sd!r}"
                )
            source_dir, pipeline_session_id = sd, None

        try:
            lifecycle.start_pipeline_preprocessing(
                job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                job_type=job_type,
            )
            lifecycle.start_pipeline_inferencing(
                job_id,
                source_dir=source_dir,
                trace_id=trace_id,
                job_type=job_type,
            )
            logger.info(
                "curation job start",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    worker_id=worker_id,
                    provider=provider,
                    model=model_name,
                    status="INFERENCING",
                    job_type=job_type,
                ),
            )
            with long_task_heartbeat(wm, status="ONLINE"):
                summary = run_curation_job(
                    conn,
                    job_id=job_id,
                    source_dir=source_dir,
                    trace_id=trace_id,
                    config_path=config_path,
                    payload=payload,
                )

            if job_type == "CURATE_SESSION" and pipeline_session_id is not None:
                finalize_session_if_needed(conn, pipeline_session_id)

            total_latency_ms = max(0, int(time.time() * 1000) - run_started_ms)
            metrics = summary.get("metrics") or {}
            succeed_payload = {
                "worker_id": worker_id,
                "session_id": session_id,
                "source_dir": source_dir,
                "trace_id": trace_id,
                "job_type": job_type,
                "curation": summary,
            }
            lifecycle.succeed(
                job_id,
                total_latency_ms=total_latency_ms,
                payload=succeed_payload,
                **JobLifecycle.claim_fence_kwargs(claimed),
            )
            wm.heartbeat(inflight=count_active_jobs_for_worker(conn, worker_id), status="ONLINE")
            logger.info(
                "curation job succeeded",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    worker_id=worker_id,
                    provider=provider,
                    model=model_name,
                    status="SUCCEEDED",
                    latency_ms=total_latency_ms,
                    job_type=job_type,
                ),
            )
            return {
                "ok": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "status": "SUCCEEDED",
                "session_id": session_id,
                "source_dir": source_dir,
                "total_latency_ms": total_latency_ms,
                "job_type": job_type,
                "curation_metrics": metrics,
                "selected": summary.get("selected"),
                "curation_artifact": summary.get("curation_artifact"),
            }
        except ClaimFenceError as fence_exc:
            logger.warning(
                "curation terminal write fenced (stale claim)",
                extra=make_log_extra(
                    job_id=job_id,
                    worker_id=worker_id,
                    trace_id=trace_id,
                    error=str(fence_exc),
                ),
            )
            return {
                "ok": False,
                "claimed": True,
                "fenced": True,
                "job_id": job_id,
                "worker_id": worker_id,
                "message": str(fence_exc),
            }
        except Exception as exc:
            self._record_pipeline_failure(
                wm=wm,
                conn=conn,
                lifecycle=lifecycle,
                job_id=job_id,
                worker_id=worker_id,
                session_id=session_id,
                trace_id=trace_id,
                provider=provider,
                model_name=model_name,
                job_type=job_type,
                run_started_ms=run_started_ms,
                exc=exc,
                namespace=claimed.get("namespace"),
                project_key=claimed.get("project_key"),
                claimed=claimed,
            )
            raise

    @staticmethod
    def _dispatch_next_pipeline_stage(conn: Any, completed_job_id: int) -> None:
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE depends_on_job_id = ?
              AND status = 'QUEUED'
              AND job_type = ?
            ORDER BY COALESCE(stage_order, 0) ASC, id ASC
            LIMIT 1
            """,
            (completed_job_id, STAGE_JOB_TYPE),
        ).fetchone()
        if row is None:
            return
        next_id = int(row["id"])
        try:
            from services.job_dispatch import send_run_jobs_for_ids
            from utils.luma_brain import cluster_headroom_for_dispatch

            h = cluster_headroom_for_dispatch(conn)
            out = send_run_jobs_for_ids(conn, [next_id])
            logger.info(
                "pipeline stage: dispatch next run_job",
                extra=make_log_extra(
                    event="chained_run_job_dispatch",
                    next_job_id=next_id,
                    completed_job_id=completed_job_id,
                    chain_policy="plan_dispatch",
                    cluster_headroom=h,
                    dispatched=out.get("dispatched"),
                    plan=out.get("plan"),
                ),
            )
        except Exception as exc:
            logger.warning(
                "failed to enqueue next PIPELINE_STAGE",
                exc_info=exc,
                extra=make_log_extra(job_id=next_id, completed_job_id=completed_job_id),
            )

    def _resolve_source_dir(
        self,
        conn: Any,
        *,
        job_id: int,
        job_type: str,
        session_id: Any,
        payload: dict[str, Any],
    ) -> tuple[str | None, int | None]:
        if job_type == "ANALYZE_SESSION":
            if session_id is None:
                raise ValueError(f"job {job_id} ANALYZE_SESSION missing session_id")
            pipeline_session_id = int(session_id)
            session_row = conn.execute(
                "SELECT previews_dir FROM sessions WHERE id = ?",
                (pipeline_session_id,),
            ).fetchone()
            if session_row is None:
                raise FileNotFoundError(
                    f"ANALYZE_SESSION job {job_id}: session id {session_id!r} not found in DB"
                )
            previews_dir = str(session_row["previews_dir"] or "").strip()
            if not previews_dir:
                raise FileNotFoundError(
                    f"ANALYZE_SESSION job {job_id}: session {session_id} has empty previews_dir"
                )
            previews_path = Path(previews_dir)
            if not previews_path.is_dir():
                raise FileNotFoundError(
                    f"ANALYZE_SESSION job {job_id}: previews_dir is not a directory or is inaccessible: "
                    f"{previews_dir!r} (resolved: {previews_path.resolve()})"
                )
            return previews_dir, pipeline_session_id
        if job_type == "ANALYZE_PATH":
            sd = payload.get("source_dir")
            if not sd:
                raise ValueError(f"ANALYZE_PATH job {job_id}: missing payload.source_dir")
            source_dir = str(sd).strip()
            if not source_dir:
                raise ValueError(f"ANALYZE_PATH job {job_id}: payload.source_dir is blank")
            src_path = Path(source_dir)
            if not src_path.is_dir():
                raise FileNotFoundError(
                    f"ANALYZE_PATH job {job_id}: source_dir is not a directory or is inaccessible: "
                    f"{source_dir!r} (resolved: {src_path.resolve()})"
                )
            return source_dir, None
        raise ValueError(f"unsupported job_type for pipeline executor: {job_type}")

    def _record_pipeline_failure(
        self,
        *,
        wm: WorkerManager,
        lifecycle: JobLifecycle,
        job_id: int,
        worker_id: int,
        session_id: Any,
        trace_id: str,
        provider: str,
        model_name: str,
        job_type: str,
        run_started_ms: int,
        exc: BaseException,
        namespace: Any = None,
        project_key: Any = None,
        conn: Any | None = None,
        claimed: dict[str, Any] | None = None,
    ) -> None:
        error_name = type(exc).__name__
        error_message = str(exc)[:500]
        bucket = classify_exception(exc)
        fail_payload = {
            "worker_id": worker_id,
            "job_type": job_type,
            "error_class": bucket,
        }
        fence = JobLifecycle.claim_fence_kwargs(claimed) if claimed else {}
        try:
            if bucket == "permanent":
                lifecycle.fail_permanent(
                    job_id,
                    error_code=error_name,
                    error_message=error_message,
                    payload=fail_payload,
                    **fence,
                )
                log_status = "FAILED_PERMANENT"
            else:
                log_status = lifecycle.fail_retryable(
                    job_id,
                    error_code=error_name,
                    error_message=error_message,
                    payload=fail_payload,
                    **fence,
                )
        except ClaimFenceError as fence_exc:
            logger.warning(
                "job failure write fenced (stale claim)",
                extra=make_log_extra(
                    job_id=job_id,
                    worker_id=worker_id,
                    trace_id=trace_id,
                    error=str(fence_exc),
                ),
            )
            return
        if conn is not None:
            live = count_active_jobs_for_worker(conn, worker_id)
        else:
            c2 = brain_connect()
            try:
                live = count_active_jobs_for_worker(c2, worker_id)
            finally:
                c2.close()
        wm.heartbeat(inflight=live, status="ERROR")
        logger.error(
            "job failed",
            extra=make_log_extra(
                trace_id=trace_id,
                job_id=job_id,
                session_id=session_id,
                worker_id=worker_id,
                provider=provider,
                model=model_name,
                status=log_status,
                latency_ms=max(0, int(time.time() * 1000) - run_started_ms),
                error_code=error_name,
                job_type=job_type,
                namespace=namespace,
                project_key=project_key,
            ),
        )
