"""
Stage-aware pipeline implementation — **recommended main path** for domain work.

Invoked by :class:`~services.job_executor.JobExecutor` after ``tasks.run_job`` claims a job
(``ANALYZE_SESSION`` / ``ANALYZE_PATH`` monolith, or per-row ``PIPELINE_STAGE``).

Flow: OpenCV gates → fast score → VLM (:mod:`inference` or ``LivehouseVLM``) →
``WRITE_ARTIFACT`` / ``analysis_results.json``; lifecycle side effects stay in ``JobLifecycle``.

State between stages: ``<source_dir>/.luma_pipeline_staged/`` (JSONL manifests).

**Compatibility façade (not a second algorithm):** :class:`~services.processor.aesthetic_pipeline.AestheticPipeline`
``.run`` and ``run_pipeline.py`` call into this module for CLI / Go mode A / tests.
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Tuple

from utils.logging_context import make_log_extra
from utils.pipeline_tracing import (
    PipelineTraceSession,
    build_trace_session,
    emit_stage3_partial_trace,
    flush_image_trace,
    make_image_trace_id,
    merge_inference_trace_attrs,
)
from services.pipeline_inference_dispatch import plan_stage3_inference_dispatch
from services.processor.stages.deep_analysis import (
    Stage3FastFirstHooks,
    analyze_with_dimensions,
    run_stage3_fast_first,
    stage3_strategy_settings,
)
from services.cache.stage3_cache import stage3_cache_from_config
from services.scheduler import DispatchPolicy, Stage3Scheduler
from services.scheduler.degrade_controller import (
    Stage3DegradeController,
    apply_top_k_fraction,
    should_run_stage3,
)
from services.scheduler.priority_queue import (
    log_top_inference_tasks,
    reorder_stage3_work_by_fast_score,
    vlm_priority_for_rank,
)
from engine.operators.stage2_prefilter import (
    assess_stage2_per_image,
    dedupe_eligible_rows_by_phash,
    log_pipeline_reduction_metrics,
)
from services.processor.pipeline_image_ops import (
    append_aesthetic_audit_line,
    apply_stage3_candidates_gating,
    assess_stage1_opencv,
    bootstrap_pipeline_layout,
    fake_result_stage1_reject,
    fake_result_stage2_reject,
    fake_result_stage2_dedupe_skip,
    fake_result_stage3_gated_skip,
    fake_result_stage3_vlm_fallback,
    merge_vlm_and_technical_scores,
    passes_stage2_thresholds,
)
from services.processor.reporting import (
    write_analysis_results_json,
    write_folder_gallery_pages,
    write_gallery_launch_scripts,
    write_preview_html_with_folders,
)
from utils.runtime_session import write_latest_session_pointer
from services.processor.pipeline_log_display import (
    build_early_reject_log_lines,
    log_photographer_summary,
    log_stage3_image_block,
    pipeline_logs_compact,
    stage3_fallback_flag,
)
from services.processor.aesthetic_pipeline import AestheticPipeline

logger = logging.getLogger(__name__)

STAGED_SUBDIR = ".luma_pipeline_staged"
ELIGIBLE_AFTER_S1 = "eligible_after_stage1.jsonl"
ELIGIBLE_AFTER_S2 = "eligible_after_stage2.jsonl"


def staged_state_dir(source_dir: Path) -> Path:
    d = source_dir / STAGED_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def list_source_images(source_dir: Path) -> List[str]:
    return sorted(
        p.name
        for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def stats_from_audit_log(log_file: Path) -> Dict[str, int]:
    """Rebuild pipeline stats dict for preview HTML from the aesthetic audit JSONL."""
    base = {"processed": 0, "failed": 0, "skipped": 0, "fast_rejected": 0, "vlm_fallback": 0, "fallback_count": 0}
    if not log_file.exists():
        return base
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except json.JSONDecodeError:
                continue
            tags = j.get("tags") or []
            if "technical_issue" in tags:
                base["fast_rejected"] += 1
            elif "low_quality" in tags:
                base["fast_rejected"] += 1
            elif "vlm_error" in tags:
                base["vlm_fallback"] += 1
            else:
                base["processed"] += 1
    return base


def _resolve_max_workers(max_workers: int | None, cfg_workers: int | None) -> int:
    cpu_workers = os.cpu_count() or 1
    preferred = max_workers if max_workers is not None else cfg_workers
    if preferred is None or preferred <= 0:
        return cpu_workers
    return preferred


def _record_model_run_for_stage3(
    conn: Any,
    *,
    job_id: int,
    provider: str,
    model_name: str,
    result: Dict[str, Any],
) -> None:
    """One ``model_runs`` row per image for STAGE3_VLM when DB connection is available."""
    from utils.luma_brain import (
        create_model_run_and_mark_started,
        mark_model_run_failed,
        mark_model_run_succeeded,
        replace_model_run_attempts,
    )
    from inference.ledger import compute_outcome_attribution

    meta = result.get("stage3_meta") or {}
    lat = meta.get("latency_ms")
    try:
        lat_i = int(float(lat))
    except (TypeError, ValueError):
        lat_i = 0

    rid = create_model_run_and_mark_started(
        conn,
        job_id=job_id,
        provider=provider or "unknown",
        model_name=model_name,
        primary_provider=provider,
        primary_model=model_name,
        prompt_length=None,
        queue_wait_ms=0,
    )
    if result.get("error"):
        err_type = str((meta.get("outcome") or "vlm_error"))[:120]
        att = [
            {
                "role": "primary",
                "provider_id": provider or "unknown",
                "model_name": model_name,
                "latency_ms": lat_i,
                "ok": False,
                "error_type": err_type,
                "error_message": (result.get("reason") or "")[:500],
            }
        ]
        replace_model_run_attempts(conn, model_run_id=rid, attempts=att)
        outcome = compute_outcome_attribution(
            ledger={"attempts": att},
            payload_status="FAILED",
        )
        mark_model_run_failed(
            conn,
            run_id=rid,
            latency_ms=lat_i,
            error_type=err_type,
            error_message=(result.get("reason") or "")[:500],
            end_to_end_latency_ms=lat_i,
            provider_latency_ms=lat_i,
            outcome_attribution=outcome,
        )
        return

    att_ok = [
        {
            "role": "primary",
            "provider_id": provider or "unknown",
            "model_name": model_name,
            "latency_ms": lat_i,
            "ok": True,
        }
    ]
    replace_model_run_attempts(conn, model_run_id=rid, attempts=att_ok)
    outcome = compute_outcome_attribution(
        ledger={"attempts": att_ok},
        payload_status="SUCCESS",
    )
    degraded_mi = bool(
        result.get("inference_degraded")
        or meta.get("outcome") == "degraded_inference"
    )
    def _coerce_tok(v: object) -> int | None:
        if v is None:
            return None
        try:
            n = int(v)  # type: ignore[arg-type]
            return n if n >= 0 else None
        except (TypeError, ValueError):
            return None

    mark_model_run_succeeded(
        conn,
        run_id=rid,
        latency_ms=lat_i,
        end_to_end_latency_ms=lat_i,
        provider_latency_ms=lat_i,
        model_name=model_name,
        final_model=model_name,
        outcome_attribution=outcome,
        degraded=1 if degraded_mi else 0,
        prompt_tokens=_coerce_tok(meta.get("prompt_tokens")),
        completion_tokens=_coerce_tok(meta.get("completion_tokens")),
    )


class PipelineStageRunner:
    """Main-path pipeline runner: stages, inference, and on-disk artifacts (production + stage jobs)."""

    def __init__(
        self,
        *,
        config_path: str,
        source_dir: str,
        trace_id: str,
        job_id: int | None,
        worker_id: int,
        session_id: int | None = None,
    ) -> None:
        self.config_path = config_path
        self.source_dir = Path(source_dir)
        self.trace_id = trace_id
        self.job_id = job_id
        self.worker_id = worker_id
        self.session_id = session_id
        self.file_lock = Lock()
        self._config: Dict[str, Any] | None = None
        self._folders: Dict[str, Path] | None = None
        self._log_paths: Dict[str, Path] | None = None
        self._pipe: AestheticPipeline | None = None
        self._last_stage1_total_images: int | None = None
        self._pt_resolved = False
        self._pt_session: PipelineTraceSession | None = None

    def _ensure_layout(self) -> None:
        if self._config is not None:
            return
        cfg, _, folders, logs = bootstrap_pipeline_layout(self.config_path, str(self.source_dir))
        self._config = cfg
        self._folders = folders
        self._log_paths = logs

    @property
    def config(self) -> Dict[str, Any]:
        self._ensure_layout()
        assert self._config is not None
        return self._config

    @property
    def folders(self) -> Dict[str, Path]:
        self._ensure_layout()
        assert self._folders is not None
        return self._folders

    @property
    def log_paths(self) -> Dict[str, Path]:
        self._ensure_layout()
        assert self._log_paths is not None
        return self._log_paths

    def _pipeline_trace_session(self) -> PipelineTraceSession | None:
        if self._pt_resolved:
            return self._pt_session
        self._pt_resolved = True
        self._ensure_layout()
        self._pt_session = build_trace_session(
            self.config,
            job_trace_id=self.trace_id,
            source_dir=self.source_dir,
        )
        return self._pt_session

    def _audit_logged_image_names(self) -> set[str]:
        """Image basenames already present in the aesthetic audit log (checkpoint / resume)."""
        self._ensure_layout()
        log_file = self.log_paths["log_file"]
        processed: set[str] = set()
        if not log_file.exists():
            return processed
        with open(log_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    img = entry.get("image")
                    if img:
                        processed.add(str(img))
                except json.JSONDecodeError:
                    continue
        return processed

    def _lazy_pipeline_for_vlm(self) -> AestheticPipeline:
        if self._pipe is None:
            self._pipe = AestheticPipeline(
                config_path=self.config_path,
                source_dir=str(self.source_dir),
                trace_id=self.trace_id,
                job_id=self.job_id,
                session_id=self.session_id,
                worker_id=self.worker_id,
            )
        return self._pipe

    def run_prepare_input(self) -> Dict[str, Any]:
        staged_state_dir(self.source_dir)
        self._ensure_layout()
        return {
            "manifest": str(staged_state_dir(self.source_dir)),
            "total_images": len(list_source_images(self.source_dir)),
        }

    def run_stage1_filter(
        self,
        *,
        max_workers: int | None = None,
        enable_checkpoint: bool = True,
    ) -> Dict[str, Any]:
        t_inf0 = time.perf_counter()
        self._ensure_layout()
        sd = staged_state_dir(self.source_dir)
        images = list_source_images(self.source_dir)
        total_roll = len(images)
        self._last_stage1_total_images = int(total_roll)
        skipped_ckpt = 0
        if enable_checkpoint:
            done = self._audit_logged_image_names()
            if done:
                images = [n for n in images if n not in done]
                skipped_ckpt = total_roll - len(images)
        eligible: List[Dict[str, Any]] = []
        if not images:
            _write_jsonl(sd / ELIGIBLE_AFTER_S1, [])
            return {
                "total": total_roll,
                "stage1_pass": 0,
                "stage1_reject": 0,
                "checkpoint_skipped": skipped_ckpt,
                "eligible_written": str(sd / ELIGIBLE_AFTER_S1),
            }

        cfg_w = self.config.get("processing", {}).get("max_workers")
        mw = _resolve_max_workers(max_workers, cfg_w)

        el_lock = Lock()
        ts = self._pipeline_trace_session()

        def assess_one(file_name: str) -> Tuple[str, bool, str, float, Dict[str, Any]]:
            rec = ts.recorder(file_name) if ts else None
            t0 = time.perf_counter()
            fp = str(self.source_dir / file_name)
            ok = False
            reason = ""
            tech_score = 0.0
            debug_info: Dict[str, Any] = {}
            try:
                ok, reason, tech_score, debug_info = assess_stage1_opencv(self.config, fp)
                return file_name, ok, reason, tech_score, debug_info
            finally:
                if rec:
                    rec.add_span(
                        "stage1",
                        start_mono=t0,
                        end_mono=time.perf_counter(),
                        attributes={
                            "opencv_pass": ok,
                            "reject_reason": (reason or "")[:120] if not ok else "",
                        },
                    )
                    flush_image_trace(ts, rec, segment="stage1")

        rejects = 0
        with ThreadPoolExecutor(max_workers=mw) as ex:
            futs = {ex.submit(assess_one, fn): fn for fn in images}
            for fut in as_completed(futs):
                fn = futs[fut]
                try:
                    file_name, ok, reason, tech_score, debug_info = fut.result()
                except Exception:
                    rejects += 1
                    fak = fake_result_stage1_reject(
                        tech_score=0.0,
                        reason="worker_error",
                        debug_info={},
                    )
                    append_aesthetic_audit_line(
                        config=self.config,
                        folders=self.folders,
                        log_paths=self.log_paths,
                        file_lock=self.file_lock,
                        image_path=str(self.source_dir / fn),
                        ai_data=fak,
                    )
                    if ts:
                        r = ts.recorder(fn)
                        if r:
                            flush_image_trace(
                                ts,
                                r,
                                segment="stage1",
                                inference_summary={"inference_status": "worker_error"},
                            )
                    continue

                fp = str(self.source_dir / file_name)
                if not ok:
                    rejects += 1
                    fak = fake_result_stage1_reject(
                        tech_score=tech_score,
                        reason=reason,
                        debug_info=debug_info,
                    )
                    append_aesthetic_audit_line(
                        config=self.config,
                        folders=self.folders,
                        log_paths=self.log_paths,
                        file_lock=self.file_lock,
                        image_path=fp,
                        ai_data=fak,
                    )
                else:
                    with el_lock:
                        eligible.append(
                            {
                                "file_name": file_name,
                                "tech_score": tech_score,
                                "debug_info": debug_info,
                            }
                        )

        eligible.sort(key=lambda x: str(x["file_name"]))
        _write_jsonl(sd / ELIGIBLE_AFTER_S1, eligible)
        inference_ms = int((time.perf_counter() - t_inf0) * 1000)
        log_pipeline_reduction_metrics(
            stage="stage1_filter",
            count_in=len(images),
            count_out=len(eligible),
            extra={"stage1_reject": rejects},
        )
        return {
            "total": total_roll,
            "stage1_pass": len(eligible),
            "stage1_reject": rejects,
            "checkpoint_skipped": skipped_ckpt,
            "inference_wall_ms": inference_ms,
            "eligible_written": str(sd / ELIGIBLE_AFTER_S1),
        }

    def run_stage2_fast_score(self, *, max_workers: int | None = None) -> Dict[str, Any]:
        t_inf0 = time.perf_counter()
        self._ensure_layout()
        sd = staged_state_dir(self.source_dir)
        m1 = sd / ELIGIBLE_AFTER_S1
        incoming = _read_jsonl(m1)

        cfg_w = self.config.get("processing", {}).get("max_workers")
        mw = _resolve_max_workers(max_workers, cfg_w)

        eligible: List[Dict[str, Any]] = []

        lock = Lock()
        rejected = 0
        ts = self._pipeline_trace_session()

        def handle_row(row: Dict[str, Any]) -> None:
            nonlocal rejected
            file_name = str(row["file_name"])
            fp = str(self.source_dir / file_name)
            tech_score = float(row["tech_score"])
            debug_info = dict(row.get("debug_info") or {})
            rec = ts.recorder(file_name) if ts else None
            t0 = time.perf_counter()

            s2 = assess_stage2_per_image(
                fp,
                self.config,
                tech_score=tech_score,
                debug_info=debug_info,
            )
            fs = s2.fast_score
            merged_dbg = {**debug_info, **s2.debug_extra}

            if not passes_stage2_thresholds(self.config, tech_score, fs):
                with lock:
                    rejected += 1
                fak = fake_result_stage2_reject(
                    tech_score=tech_score,
                    fast_score=fs,
                    debug_info=merged_dbg,
                )
                append_aesthetic_audit_line(
                    config=self.config,
                    folders=self.folders,
                    log_paths=self.log_paths,
                    file_lock=self.file_lock,
                    image_path=fp,
                    ai_data=fak,
                )
                if rec:
                    rec.add_span(
                        "stage2",
                        start_mono=t0,
                        end_mono=time.perf_counter(),
                        attributes={"reject": "threshold"},
                    )
                    flush_image_trace(ts, rec, segment="stage2")
                return

            if not s2.prefilter_ok:
                with lock:
                    rejected += 1
                fak = fake_result_stage2_reject(
                    tech_score=tech_score,
                    fast_score=fs,
                    debug_info=merged_dbg,
                    reject_detail=str(s2.prefilter_reason or "stage2_prefilter"),
                )
                append_aesthetic_audit_line(
                    config=self.config,
                    folders=self.folders,
                    log_paths=self.log_paths,
                    file_lock=self.file_lock,
                    image_path=fp,
                    ai_data=fak,
                )
                if rec:
                    rec.add_span(
                        "stage2",
                        start_mono=t0,
                        end_mono=time.perf_counter(),
                        attributes={"reject": "prefilter"},
                    )
                    flush_image_trace(ts, rec, segment="stage2")
                return

            row_out = {
                "file_name": file_name,
                "tech_score": tech_score,
                "fast_score": fs,
                "debug_info": merged_dbg,
                "phash": int(s2.phash),
            }
            with lock:
                eligible.append(row_out)
            if rec:
                rec.add_span(
                    "stage2",
                    start_mono=t0,
                    end_mono=time.perf_counter(),
                    attributes={"pass": True},
                )
                flush_image_trace(ts, rec, segment="stage2")

        if not incoming:
            _write_jsonl(sd / ELIGIBLE_AFTER_S2, [])
            return {"total_in": 0, "stage2_pass": 0, "stage2_reject": 0, "inference_wall_ms": 0}

        pool_n = min(mw, max(1, len(incoming)))
        logger.info(
            "stage2 parallel scoring begin | images=%s workers=%s source=%s",
            len(incoming),
            pool_n,
            self.source_dir,
        )
        stall_sec = max(
            60.0,
            float(os.getenv("LIVEHOUSE_STAGE2_STALL_TIMEOUT_SEC", "120")),
        )
        with ThreadPoolExecutor(max_workers=pool_n) as ex:
            futs = [ex.submit(handle_row, r) for r in incoming]
            pending = set(futs)
            while pending:
                done, pending = wait(pending, timeout=stall_sec, return_when=FIRST_COMPLETED)
                if not done:
                    logger.error(
                        "stage2 scoring stalled (no image finished in %.0fs); "
                        "pending=%s — restart Celery if workers stuck in OpenCV/OpenCL",
                        stall_sec,
                        len(pending),
                    )
                    raise TimeoutError(
                        f"stage2 scoring stalled with {len(pending)} image(s) pending"
                    )
                for fu in done:
                    fu.result()

        eligible.sort(key=lambda x: str(x["file_name"]))
        pre_dedupe_n = len(eligible)
        eligible, removed_dedup, dedup_diag = dedupe_eligible_rows_by_phash(eligible, self.config)
        if dedup_diag.get("enabled"):
            log_pipeline_reduction_metrics(
                stage="stage2_after_phash_dedup",
                count_in=pre_dedupe_n,
                count_out=len(eligible),
                extra=dedup_diag,
            )
            for row in removed_dedup:
                fp = str(self.source_dir / row["file_name"])
                fak = fake_result_stage2_dedupe_skip(
                    tech_score=float(row["tech_score"]),
                    fast_score=float(row["fast_score"]),
                    debug_info=dict(row.get("debug_info") or {}),
                )
                append_aesthetic_audit_line(
                    config=self.config,
                    folders=self.folders,
                    log_paths=self.log_paths,
                    file_lock=self.file_lock,
                    image_path=fp,
                    ai_data=fak,
                )
                if ts:
                    r = ts.recorder(str(row["file_name"]))
                    if r:
                        tq = time.perf_counter()
                        r.add_span(
                            "stage2_dedupe",
                            start_mono=tq,
                            end_mono=time.perf_counter(),
                            attributes={"reason": "phash_dedupe"},
                        )
                        flush_image_trace(ts, r, segment="stage2")

        log_pipeline_reduction_metrics(
            stage="stage2_after_score_prefilter",
            count_in=len(incoming),
            count_out=pre_dedupe_n,
            extra={"stage2_reject": rejected},
        )

        scale_n = self._last_stage1_total_images if self._last_stage1_total_images else len(incoming)
        eligible, skipped_gate, gate_diag = apply_stage3_candidates_gating(
            eligible,
            config=self.config,
            batch_input_scale_n=int(scale_n),
        )
        if gate_diag["before"] != gate_diag["after"]:
            logger.info(
                "[Stage2] filtered: %s → %s for Stage3",
                gate_diag["before"],
                gate_diag["after"],
            )
        log_pipeline_reduction_metrics(
            stage="stage3_admission",
            count_in=gate_diag.get("before", len(eligible)),
            count_out=gate_diag.get("after", len(eligible)),
            extra={
                "stage3_gating": {
                    "stage3_threshold": gate_diag.get("stage3_threshold"),
                    "stage3_threshold_effective": gate_diag.get("stage3_threshold_effective"),
                    "top_k_ratio": gate_diag.get("top_k_ratio"),
                    "top_k_ratio_effective": gate_diag.get("top_k_ratio_effective"),
                    "pipeline_mode": gate_diag.get("pipeline_mode"),
                    "gating_source": gate_diag.get("gating_source"),
                    "admission_percentile": gate_diag.get("admission_percentile"),
                    "stage3_inferences_saved": gate_diag.get("stage3_inferences_saved"),
                    "estimated_gpu_seconds_saved": gate_diag.get("estimated_gpu_seconds_saved"),
                },
                "images_entering_stage3": gate_diag.get("after", len(eligible)),
            },
        )
        if int(gate_diag.get("before", 0)) > 0:
            ap = gate_diag.get("admission_percentile")
            logger.info(
                "[Stage3 admission] saved_inferences=%s est_gpu_sec_saved=%.1f "
                "admission_pct=%s eff_thresh=%.4f eff_top_k=%.4f batch_scale_n=%s",
                gate_diag.get("stage3_inferences_saved"),
                float(gate_diag.get("estimated_gpu_seconds_saved") or 0.0),
                f"{float(ap):.2f}" if ap is not None else "n/a",
                float(gate_diag.get("stage3_threshold_effective") or gate_diag.get("stage3_threshold") or 0.0),
                float(gate_diag.get("top_k_ratio_effective") or gate_diag.get("top_k_ratio") or 0.0),
                gate_diag.get("batch_input_scale_n"),
            )
        for row in list(eligible) + skipped_gate:
            row.pop("phash", None)

        for row in skipped_gate:
            fp = str(self.source_dir / row["file_name"])
            fak = fake_result_stage3_gated_skip(
                tech_score=float(row["tech_score"]),
                fast_score=float(row["fast_score"]),
                debug_info=dict(row.get("debug_info") or {}),
            )
            append_aesthetic_audit_line(
                config=self.config,
                folders=self.folders,
                log_paths=self.log_paths,
                file_lock=self.file_lock,
                image_path=fp,
                ai_data=fak,
            )
            if ts:
                r = ts.recorder(str(row["file_name"]))
                if r:
                    tg = time.perf_counter()
                    r.add_span(
                        "stage2_gate",
                        start_mono=tg,
                        end_mono=time.perf_counter(),
                        attributes={"reason": "stage3_admission_gate"},
                    )
                    flush_image_trace(ts, r, segment="stage2")

        _write_jsonl(sd / ELIGIBLE_AFTER_S2, eligible)
        inference_ms = int((time.perf_counter() - t_inf0) * 1000)
        out_stage2: Dict[str, Any] = {
            "total_in": len(incoming),
            "stage2_pass": len(eligible),
            "stage2_reject": rejected,
            "inference_wall_ms": inference_ms,
            "eligible_written": str(sd / ELIGIBLE_AFTER_S2),
        }
        if gate_diag["before"] != gate_diag["after"] or skipped_gate:
            out_stage2["stage3_gating"] = gate_diag
            out_stage2["stage2_pre_gate_eligible"] = gate_diag["before"]
        return out_stage2

    def run_stage3_vlm(
        self,
        *,
        max_workers: int | None = None,
        conn: Any | None = None,
        dispatch_policy: DispatchPolicy | None = None,
        stage3_time_budget_seconds: float | None = None,
        estimated_vlm_seconds: float = 45.0,
    ) -> Dict[str, Any]:
        t_inf0 = time.perf_counter()
        pipe = self._lazy_pipeline_for_vlm()
        stage3_cache = stage3_cache_from_config(pipe.config)
        sd = staged_state_dir(self.source_dir)
        m2 = sd / ELIGIBLE_AFTER_S2
        incoming = _read_jsonl(m2)

        cfg_w = pipe.config.get("processing", {}).get("max_workers")
        mw = _resolve_max_workers(max_workers, cfg_w)

        processed = fb = failed = fcb = 0
        errs: List[str] = []
        tally = Lock()
        stage3_early_exit_payload: Dict[str, Any] | None = None

        total = len(incoming)

        infer_extra_holder: List[Dict[str, Any] | None] = [None]
        dsel: dict[str, bool] = {}

        def one(process_index: int, row: Dict[str, Any], *, vlm_queue_priority: int) -> None:
            nonlocal processed, fb, failed, fcb
            file_name = str(row["file_name"])
            fp = str(self.source_dir / file_name)
            tech_score = float(row["tech_score"])
            debug_info = dict(row.get("debug_info") or {})
            blur_type = (debug_info or {}).get("blur_type")
            prog = f"{process_index}/{total}"

            ts = self._pipeline_trace_session()
            rec = ts.recorder(file_name) if ts else None
            t_wall0 = time.perf_counter()
            itid = make_image_trace_id(self.trace_id, file_name)
            merged_infer = merge_inference_trace_attrs(
                dict(infer_extra_holder[0] or {}),
                image_trace_id=itid,
                job_trace_id=self.trace_id,
                file_name=file_name,
            )

            def _emit_stage3_trace(result_dict: Dict[str, Any] | None, *, pipeline_err: str | None = None) -> None:
                if rec is None or ts is None:
                    return
                t1 = time.perf_counter()
                rec.add_span(
                    "stage3",
                    start_mono=t_wall0,
                    end_mono=t1,
                    attributes={
                        "pipeline_error": bool(pipeline_err),
                        "error_tag": (pipeline_err or "")[:120],
                    },
                )
                sm = (result_dict or {}).get("stage3_meta") or {}
                lb = sm.get("latency_breakdown") if isinstance(sm.get("latency_breakdown"), dict) else {}
                qw = float(lb.get("queue_wait_sec") or 0.0)
                mi = float(lb.get("model_infer_sec") or 0.0)
                po = float(lb.get("postprocess_sec") or 0.0)
                if qw + mi + po > 1e-6:
                    rec.add_inference_subspans(
                        parent_start_mono=t_wall0,
                        parent_end_mono=t1,
                        queue_wait_sec=qw,
                        model_infer_sec=mi,
                        postprocess_sec=po,
                    )
                if ts.debug and dsel:
                    rec.record_routing({"dispatch_selected_for_inference": bool(dsel.get(file_name))})
                inf = {
                    "queue_wait_ms": int(qw * 1000),
                    "model_infer_ms": int(mi * 1000),
                    "postprocess_ms": int(po * 1000),
                    "vlm_retry_count": int(sm.get("attempt") or 0),
                    "router_fallback": bool((result_dict or {}).get("inference_degraded")),
                    "cache_hit": str(sm.get("outcome") or "") == "cache_hit",
                    "stage3_outcome": str(sm.get("outcome") or ""),
                }
                flush_image_trace(ts, rec, segment="stage3", inference_summary=inf)

            logger.info(
                "stage3 inference start",
                extra=make_log_extra(
                    trace_id=self.trace_id,
                    job_id=self.job_id,
                    session_id=self.session_id,
                    worker_id=self.worker_id,
                    provider=pipe.model_provider,
                    model=pipe.model_name,
                    status="INFERENCING",
                    image_trace_id=itid,
                ),
            )
            try:
                phash = int((debug_info or {}).get("phash", 0) or 0)
                result = analyze_with_dimensions(
                    pipe.vlm,
                    pipe.config,
                    fp,
                    blur_type=blur_type,
                    queue_priority=vlm_queue_priority,
                    stage1_features=debug_info,
                    log_context={
                        "trace_id": self.trace_id,
                        "job_id": self.job_id,
                        "session_id": self.session_id,
                        "photo_id": None,
                        "worker_id": self.worker_id,
                        "provider": pipe.model_provider,
                        "model": pipe.model_name,
                        "image_trace_id": itid,
                    },
                    inference_extra_metadata=merged_infer,
                    stage3_cache=stage3_cache,
                    image_phash=phash,
                )
            except Exception as exc:
                with tally:
                    failed += 1
                    errs.append(f"{file_name}: {exc}")
                append_aesthetic_audit_line(
                    config=pipe.config,
                    folders=pipe.folders,
                    log_paths=pipe.log_paths,
                    file_lock=pipe.file_lock,
                    image_path=fp,
                    ai_data={
                        "score": 0.0,
                        "reason": str(exc),
                        "tags": ["pipeline_error"],
                        "dimensions": {},
                        "weakness": str(type(exc).__name__),
                        "debug_info": debug_info,
                    },
                )
                err_snap = {
                    "score": 0.0,
                    "reason": str(exc),
                    "tags": ["pipeline_error"],
                    "dimensions": {},
                    "weakness": str(type(exc).__name__),
                    "debug_info": debug_info,
                }
                logger.error(
                    "\n".join(
                        build_early_reject_log_lines(
                            file_name,
                            err_snap,
                            progress=prog,
                            config=pipe.config,
                            route_note=f"❌ pipeline_error: {type(exc).__name__}",
                        )
                    )
                )
                _emit_stage3_trace(None, pipeline_err=str(exc))
                return

            if conn is not None and self.job_id is not None and int(self.job_id) > 0:
                try:
                    _record_model_run_for_stage3(
                        conn,
                        job_id=int(self.job_id),
                        provider=str(pipe.model_provider),
                        model_name=str(pipe.model_name),
                        result=result,
                    )
                except Exception:
                    logger.exception("model_run ledger failed", extra=make_log_extra(job_id=self.job_id))

            if result.get("error"):
                with tally:
                    fb += 1
                    processed += 1
                fak = fake_result_stage3_vlm_fallback(
                    tech_score=tech_score,
                    reason_txt=str(result.get("reason") or "unknown"),
                    debug_info=debug_info,
                )
                append_aesthetic_audit_line(
                    config=pipe.config,
                    folders=pipe.folders,
                    log_paths=pipe.log_paths,
                    file_lock=pipe.file_lock,
                    image_path=fp,
                    ai_data=fak,
                )
                logger.warning(
                    "\n".join(
                        build_early_reject_log_lines(
                            file_name,
                            fak,
                            progress=prog,
                            config=pipe.config,
                            route_note=f"⚠️ Stage 3: {str(result.get('reason') or 'unknown')[:120]}",
                        )
                    )
                )
                _emit_stage3_trace(result)
                return

            degraded_ok = bool(result.get("inference_degraded")) or (
                (result.get("stage3_meta") or {}).get("outcome") == "degraded_inference"
            )
            merged = merge_vlm_and_technical_scores(pipe.config, result, tech_score, debug_info)
            append_aesthetic_audit_line(
                config=pipe.config,
                folders=pipe.folders,
                log_paths=pipe.log_paths,
                file_lock=pipe.file_lock,
                image_path=fp,
                ai_data=merged,
            )
            smm = merged.get("stage3_meta") or {}
            lat_s = float(smm.get("latency_ms") or 0) / 1000.0
            lb = smm.get("latency_breakdown") or {}
            fb_log = stage3_fallback_flag(merged)
            if isinstance(lb, dict) and lb.get("queue_wait_sec") is not None:
                logger.info(
                    "[Stage3] image=%s total=%.2fs queue_wait=%.2fs model_infer=%.2fs postprocess=%.2fs fallback=%s",
                    file_name,
                    lat_s,
                    float(lb.get("queue_wait_sec") or 0),
                    float(lb.get("model_infer_sec") or 0),
                    float(lb.get("postprocess_sec") or 0),
                    fb_log,
                )
            else:
                logger.info("[Stage3] image=%s latency=%.2fs fallback=%s", file_name, lat_s, fb_log)
            log_stage3_image_block(logger, file_name, merged, prog, pipe.config)
            _emit_stage3_trace(merged)
            with tally:
                processed += 1
                if degraded_ok:
                    fcb += 1

        if not incoming:
            return {
                "total_in": 0,
                "processed": 0,
                "failed": 0,
                "vlm_fallback": 0,
                "fallback_count": 0,
                "inference_wall_ms": 0,
                **(
                    {"stage3_vlm_cache": stage3_cache.metrics_dict()}
                    if stage3_cache is not None
                    else {}
                ),
            }

        incoming_sorted = sorted(incoming, key=lambda x: str(x["file_name"]))
        deferred: List[Dict[str, Any]] = []
        work_items: List[Tuple[int, Dict[str, Any]]] = []
        dispatch_plan_dict: Dict[str, Any] | None = None

        if conn is not None and self.job_id is not None and int(self.job_id) > 0:
            plan, incoming_sorted, selected_ids = plan_stage3_inference_dispatch(
                conn,
                incoming_sorted,
                job_id=int(self.job_id),
                provider=str(pipe.model_provider),
                policy=dispatch_policy,
            )
            dispatch_plan_dict = plan.to_log_dict()
            logger.info(
                "stage3 dispatch plan",
                extra={"dispatch_plan": dispatch_plan_dict},
            )
            skipped_n = int(plan.candidate_count) - len(plan.selected_job_ids)
            logger.info(
                "stage3 dispatch summary selected=%s skipped=%s headroom=%s effective_max=%s "
                "provider_effective_caps=%s note=%s",
                len(plan.selected_job_ids),
                skipped_n,
                plan.headroom,
                plan.effective_max,
                plan.provider_effective_caps,
                plan.note,
            )
            work_items = [
                (int(jid), incoming_sorted[int(jid) - 1]) for jid in plan.selected_job_ids
            ]
            deferred = [
                row
                for i, row in enumerate(incoming_sorted, start=1)
                if i not in selected_ids
            ]
        else:
            work_items = list(enumerate(incoming_sorted, start=1))

        work_items, stage3_tasks_for_log = reorder_stage3_work_by_fast_score(
            work_items,
            source_dir=self.source_dir,
        )
        log_top_inference_tasks(
            logger,
            stage3_tasks_for_log,
            n=10,
            label="stage3_pipeline",
        )

        deg_ctl = Stage3DegradeController.from_processing_config(pipe.config.get("processing") or {})
        deg_decision = deg_ctl.evaluate()
        infer_extra_holder[0] = dict(deg_decision.inference_extra_metadata)

        if not should_run_stage3(deg_decision):
            logger.warning(
                "stage3 degrade: skip entire batch reasons=%s queue_depth=%s avg_latency_ms=%s",
                list(deg_decision.reasons),
                deg_decision.queue_depth,
                deg_decision.avg_latency_ms,
            )
            deferred.extend([row for (_, row) in work_items])
            work_items = []
        elif deg_decision.top_k_fraction < 1.0:
            cap_n = apply_top_k_fraction(len(work_items), deg_decision.top_k_fraction)
            if cap_n < len(work_items):
                tail = work_items[cap_n:]
                deferred.extend([row for (_, row) in tail])
                work_items = work_items[:cap_n]
                logger.warning(
                    "stage3 degrade: trim candidates kept=%s deferred_extra=%s reasons=%s",
                    len(work_items),
                    len(tail),
                    list(deg_decision.reasons),
                )

        dsel.clear()
        for _jid, row in work_items:
            dsel[str(row["file_name"])] = True
        for row in incoming_sorted:
            fn = str(row["file_name"])
            if fn not in dsel:
                dsel[fn] = False

        n_work = len(work_items)
        budget_sched: Stage3Scheduler | None = None
        if stage3_time_budget_seconds is not None:
            budget_sched = Stage3Scheduler(
                time_budget_seconds=float(stage3_time_budget_seconds),
                estimated_inference_seconds=float(estimated_vlm_seconds),
            )
            logger.info(
                "stage3 budget gate: budget_sec=%.1f estimated_inference_sec=%.1f planned_candidates=%s",
                budget_sched.time_budget_seconds,
                budget_sched.estimated_inference_seconds,
                n_work,
            )

        s3_strat = stage3_strategy_settings(pipe.config)
        use_fast_first = (
            s3_strat["strategy"] != "full_only"
            and budget_sched is None
            and n_work > 0
        )

        if use_fast_first:
            logger.info("stage3 runner: fast_first dual-mode (batch n=%s)", n_work)
            mq = int((pipe.config.get("model") or {}).get("max_inference_queue_size", 16) or 16)
            pool_workers_ff = max(1, min(mw, mq, n_work))
            tasks3_ff: List[Tuple[Any, ...]] = []
            for rank, (_jid, row) in enumerate(work_items, start=1):
                fn = str(row["file_name"])
                fp = str(self.source_dir / fn)
                vp = vlm_priority_for_rank(rank_one_based=rank, batch_size=n_work)
                tasks3_ff.append(
                    (
                        rank,
                        n_work,
                        fn,
                        fp,
                        float(row["tech_score"]),
                        dict(row.get("debug_info") or {}),
                        vp,
                        stage3_cache,
                    )
                )
            stats_ff = {
                "processed": 0,
                "vlm_fallback": 0,
                "fallback_count": 0,
                "stage3_latencies_sec": [],
                "stage3_fast_pass_latencies_sec": [],
                "stage3_full_pass_latencies_sec": [],
                "stage3_wall_latencies_sec": [],
                "stage3_fast_only_count": 0,
                "stage3_full_count": 0,
                "stage3_early_exit_ratio": 0.0,
            }
            _ff_job_id = self.job_id
            _ff_provider = str(pipe.model_provider)
            _ff_model = str(pipe.model_name)

            def _record_model_run_ff(_file_path: str, _result: Dict[str, Any]) -> None:
                """Per-image model_run ledger write for the fast_first path.

                Opens its own connection so concurrent future callbacks are safe.
                """
                if _ff_job_id is None or int(_ff_job_id) <= 0:
                    return
                from utils.luma_brain import brain_connect as _bc
                _c = _bc()
                try:
                    _record_model_run_for_stage3(
                        _c,
                        job_id=int(_ff_job_id),
                        provider=_ff_provider,
                        model_name=_ff_model,
                        result=_result,
                    )
                finally:
                    _c.close()

            hooks_ff = Stage3FastFirstHooks(
                append_audit_line=lambda pth, ai: append_aesthetic_audit_line(
                    config=pipe.config,
                    folders=pipe.folders,
                    log_paths=pipe.log_paths,
                    file_lock=pipe.file_lock,
                    image_path=pth,
                    ai_data=ai,
                ),
                progress_lock=tally,
                stats=stats_ff,
                trace_id=self.trace_id,
                job_id=self.job_id,
                session_id=self.session_id,
                photo_id=None,
                worker_id=self.worker_id,
                model_provider=str(pipe.model_provider),
                model_name=str(pipe.model_name),
                pipeline_trace_session=self._pipeline_trace_session(),
                record_model_run=_record_model_run_ff,
            )
            run_stage3_fast_first(
                pipe.vlm,
                pipe.config,
                tasks3_ff,
                pool_workers_ff,
                s3_strat,
                hooks_ff,
                logger_obj=logger,
            )
            stage3_early_exit_payload = {
                "fast_only_count": int(stats_ff.get("stage3_fast_only_count", 0)),
                "full_count": int(stats_ff.get("stage3_full_count", 0)),
                "early_exit_ratio": float(stats_ff.get("stage3_early_exit_ratio", 0.0)),
            }
            processed = int(stats_ff.get("processed", 0))
            fb = int(stats_ff.get("vlm_fallback", 0))
            fcb = int(stats_ff.get("fallback_count", 0))
            inference_ms = int((time.perf_counter() - t_inf0) * 1000)
        elif budget_sched is not None:
            deferred_budget_tail: List[Dict[str, Any]] = []
            for i, (_jid, row) in enumerate(work_items):
                if not budget_sched.should_continue():
                    budget_sched.set_early_stop("budget_insufficient_for_next_inference")
                    deferred_budget_tail = [r for (_, r) in work_items[i:]]
                    break
                rank = i + 1
                vp = vlm_priority_for_rank(rank_one_based=rank, batch_size=n_work)
                one(rank, row, vlm_queue_priority=vp)
            if deferred_budget_tail:
                budget_sched.mark_skipped_candidates(len(deferred_budget_tail))
                deferred.extend(deferred_budget_tail)
            inference_ms_budget = int((time.perf_counter() - t_inf0) * 1000)
            budget_sched.log_summary(lg=logger, processed_override=processed)
            inference_ms = inference_ms_budget
        else:
            pool_workers = min(mw, max(1, n_work)) if n_work else 1
            with ThreadPoolExecutor(max_workers=pool_workers) as ex:
                futures = []
                for rank, (_jid, row) in enumerate(work_items, start=1):
                    vp = vlm_priority_for_rank(rank_one_based=rank, batch_size=n_work)
                    futures.append(ex.submit(one, rank, row, vlm_queue_priority=vp))
                for fu in as_completed(futures):
                    fu.result()
            inference_ms = int((time.perf_counter() - t_inf0) * 1000)

        if conn is not None:
            _write_jsonl(m2, deferred)

        out: Dict[str, Any] = {
            "total_in": total,
            "processed": processed,
            "failed": failed,
            "vlm_fallback": fb,
            "fallback_count": fcb,
            "inference_wall_ms": inference_ms,
            "stage3_degrade": {
                "level": deg_decision.level.value,
                "run_stage3": deg_decision.run_stage3,
                "reasons": list(deg_decision.reasons),
                "queue_depth": deg_decision.queue_depth,
                "avg_latency_ms": deg_decision.avg_latency_ms,
                "top_k_fraction": deg_decision.top_k_fraction,
                "thumbnail_max_side": deg_decision.thumbnail_max_side,
            },
        }
        if dispatch_plan_dict is not None:
            out["dispatch_plan"] = dispatch_plan_dict
            out["stage3_deferred_count"] = len(deferred)
            out["stage3_inference_this_round"] = len(work_items)
        if budget_sched is not None:
            out["stage3_time_budget_seconds"] = budget_sched.time_budget_seconds
            out["stage3_estimated_vlm_seconds"] = budget_sched.estimated_inference_seconds
            out["stage3_budget_early_stop_reason"] = budget_sched.early_stop_reason
            out["stage3_budget_deferred_candidates"] = budget_sched.deferred_count
        if stage3_cache is not None:
            out["stage3_vlm_cache"] = stage3_cache.metrics_dict()
            logger.info("stage3_vlm_cache | %s", out["stage3_vlm_cache"])
            stage3_cache.maybe_persist()
        if errs:
            out["errors_sample"] = errs[:10]
        if stage3_early_exit_payload is not None:
            out["stage3_early_exit"] = stage3_early_exit_payload
        return out

    def run_write_artifact(self) -> Dict[str, Any]:
        pipe = self._lazy_pipeline_for_vlm()
        log_file = pipe.log_paths["log_file"]
        stats = stats_from_audit_log(log_file)
        t0 = time.perf_counter()
        preview_path = write_preview_html_with_folders(pipe.source_dir, stats, pipe.folders)

        folder_gallery_files = write_folder_gallery_pages(
            pipe.source_dir, pipe.folders, log_file, config=pipe.config
        )
        folder_galleries_meta: List[Dict[str, Any]] = []
        for p in folder_gallery_files:
            matched = False
            for cat, fpath in pipe.folders.items():
                try:
                    if p.parent.resolve() == fpath.resolve():
                        folder_galleries_meta.append({"category": cat, "path": str(p)})
                        matched = True
                        break
                except OSError:
                    continue
            if not matched:
                folder_galleries_meta.append({"path": str(p)})

        analysis_path = write_analysis_results_json(pipe.source_dir, pipe.folders, log_file)
        launch_script_paths = write_gallery_launch_scripts(pipe.source_dir)
        write_latest_session_pointer(pipe.source_dir)
        elapsed = time.perf_counter() - t0
        log_photographer_summary(
            logger,
            folders=pipe.folders,
            log_file=log_file,
            compact=pipeline_logs_compact(pipe.config),
            config=pipe.config,
            pipeline_stats=getattr(pipe, "stats", None),
        )
        artifact_paths = {
            "analysis_results": str(analysis_path) if analysis_path else None,
            "preview_html": str(preview_path) if preview_path else None,
            "folder_galleries": folder_galleries_meta,
            "launch_scripts": [str(p) for p in launch_script_paths] if launch_script_paths else [],
        }
        return {
            "artifact_paths": artifact_paths,
            "stats": stats,
            "writing_wall_seconds": round(elapsed, 3),
        }


def finalize_session_if_needed(conn: Any, session_id: int | None) -> None:
    """Mark session photos ``ANALYZED`` (outcome ledger); pipeline stages use ``jobs`` for execution SSOT."""
    from utils.luma_brain import finalize_analyzed

    if session_id is None:
        return
    finalize_analyzed(conn, [int(session_id)])
