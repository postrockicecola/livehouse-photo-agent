"""
Fast Stage1 (OpenCV) + Stage2 (aesthetic) pipeline with global ranking — no VLM / Stage3.

Outputs under ``source_dir``:

- ``AI_Best_fast/``, ``AI_Keep_fast/``, ``AI_Trash/``
- ``analysis_fast.json`` (timings, throughput, per-image records)
"""
from __future__ import annotations

import json
import logging
import math
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Literal, Optional

from engine.operators.stage2_prefilter import assess_stage2_per_image
from utils.config_loader import ConfigLoader
from utils.json_safe import json_safe
from services.processor.pipeline_image_ops import (
    assess_stage1_opencv,
    passes_stage2_thresholds,
)

logger = logging.getLogger(__name__)

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")

FastStatus = Literal[
    "stage1_reject",
    "stage2_threshold_reject",
    "stage2_prefilter_reject",
    "eligible",
    "failed",
]


FAST_FOLDERS = {
    "best": "AI_Best_fast",
    "keep": "AI_Keep_fast",
    "trash": "AI_Trash",
}


def _list_image_files(source_dir: Path) -> List[Path]:
    return sorted(
        p
        for p in source_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )


def _load_config_merged(config_path: str, source_dir: str) -> Dict[str, Any]:
    cfg = ConfigLoader.load(config_path)
    cfg["paths"]["source_dir"] = source_dir
    cfg["paths"]["folders"] = dict(FAST_FOLDERS)
    return cfg


def _fast_folder_paths(base: Path) -> Dict[str, Path]:
    """Fixed output dirs for the fast-only run (YAML ``paths.folders`` overridden)."""
    return {key: base / name for key, name in FAST_FOLDERS.items()}


def _ensure_output_dirs(folders: Dict[str, Path]) -> None:
    for p in folders.values():
        p.mkdir(parents=True, exist_ok=True)


@dataclass
class _WorkerRow:
    """One image after Stage1 + Stage2 scoring (no global rank yet)."""

    file_name: str
    path: str
    status: FastStatus
    tech_score: Optional[float]
    fast_score: Optional[float]
    stage1_seconds: float
    stage2_seconds: float
    reason: str
    debug_info: Dict[str, Any]
    error: Optional[str] = None


@dataclass
class FastPipelineResult:
    """Outcome of :func:`run_fast_pipeline`."""

    analysis_path: Path
    folders: Dict[str, Path]
    total_images: int
    wall_seconds: float
    throughput_ips: float
    summary: Dict[str, Any] = field(default_factory=dict)


def _process_one_image(
    *,
    file_path: str,
    config: Dict[str, Any],
) -> _WorkerRow:
    file_name = Path(file_path).name

    try:
        t_s1_0 = time.perf_counter()
        passes_quality, reason, tech_score, debug_info = assess_stage1_opencv(config, file_path)
        t_after_s1 = time.perf_counter()

        if not passes_quality:
            return _WorkerRow(
                file_name=file_name,
                path=file_path,
                status="stage1_reject",
                tech_score=float(tech_score),
                fast_score=None,
                stage1_seconds=t_after_s1 - t_s1_0,
                stage2_seconds=0.0,
                reason=str(reason),
                debug_info=dict(debug_info or {}),
            )

        t_s2_0 = time.perf_counter()
        s2 = assess_stage2_per_image(
            file_path,
            config,
            tech_score=float(tech_score),
            debug_info=debug_info,
        )
        fs = float(s2.fast_score)
        merged_dbg = {**dict(debug_info or {}), **s2.debug_extra}
        t_after_s2 = time.perf_counter()

        if not passes_stage2_thresholds(config, float(tech_score), fs):
            return _WorkerRow(
                file_name=file_name,
                path=file_path,
                status="stage2_threshold_reject",
                tech_score=float(tech_score),
                fast_score=fs,
                stage1_seconds=t_s2_0 - t_s1_0,
                stage2_seconds=t_after_s2 - t_s2_0,
                reason="below_stage2_thresholds",
                debug_info=merged_dbg,
            )

        if not s2.prefilter_ok:
            return _WorkerRow(
                file_name=file_name,
                path=file_path,
                status="stage2_prefilter_reject",
                tech_score=float(tech_score),
                fast_score=fs,
                stage1_seconds=t_s2_0 - t_s1_0,
                stage2_seconds=t_after_s2 - t_s2_0,
                reason=str(s2.prefilter_reason or "stage2_prefilter"),
                debug_info=merged_dbg,
            )

        return _WorkerRow(
            file_name=file_name,
            path=file_path,
            status="eligible",
            tech_score=float(tech_score),
            fast_score=fs,
            stage1_seconds=t_s2_0 - t_s1_0,
            stage2_seconds=t_after_s2 - t_s2_0,
            reason="",
            debug_info=merged_dbg,
        )

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("fast_pipeline failed for %s", file_name)
        return _WorkerRow(
            file_name=file_name,
            path=file_path,
            status="failed",
            tech_score=None,
            fast_score=None,
            stage1_seconds=0.0,
            stage2_seconds=0.0,
            reason="exception",
            debug_info={},
            error=f"{type(exc).__name__}: {exc}",
        )


def _bucket_eligible(
    eligible: List[_WorkerRow],
    *,
    best_ratio: float,
    keep_ratio: float,
) -> tuple[List[_WorkerRow], List[_WorkerRow], List[_WorkerRow], Dict[str, int]]:
    """Sort by Stage2 ``fast_score`` descending; split into best / keep / remaining.

    ``pool_rank_by_name`` maps each eligible file name to 1-based rank among the pool (1 = best).
    """
    pool = sorted(eligible, key=lambda r: float(r.fast_score or 0.0), reverse=True)
    n = len(pool)
    if n == 0:
        return [], [], [], {}

    pool_rank_by_name = {pool[i].file_name: i + 1 for i in range(n)}

    n_best = max(0, min(n, math.ceil(n * float(best_ratio))))
    n_keep = max(0, min(n - n_best, math.ceil(n * float(keep_ratio))))

    best = pool[:n_best]
    keep = pool[n_best : n_best + n_keep]
    rest = pool[n_best + n_keep :]
    return best, keep, rest, pool_rank_by_name


def _ai_record_for_row(
    row: _WorkerRow, classification: str, pool_rank: Optional[int]
) -> Dict[str, Any]:
    ts = row.tech_score
    fs = row.fast_score
    combined = None
    if ts is not None and fs is not None:
        combined = float(ts) * 0.6 + float(fs) * 0.4

    out: Dict[str, Any] = {
        "file_name": row.file_name,
        "path": row.path,
        "classification": classification,
        "status": row.status,
        "tech_score": ts,
        "fast_score": fs,
        "combined_fast_score": combined,
        "stage1_seconds": round(row.stage1_seconds, 6),
        "stage2_seconds": round(row.stage2_seconds, 6),
        "eligible_pool_rank_by_fast_score": pool_rank,
    }
    if row.reason:
        out["reason"] = row.reason
    if row.debug_info:
        out["debug_info"] = json_safe(row.debug_info)
    if row.error:
        out["error"] = row.error
    return out


def _copy_one(src: str, dest_dir: Path, lock: Optional[Lock]) -> None:
    dest = dest_dir / Path(src).name

    def _do() -> None:
        shutil.copy2(src, dest)

    if lock is not None:
        with lock:
            _do()
    else:
        _do()


def run_fast_pipeline(
    *,
    source_dir: str,
    config_path: str | None = None,
    best_ratio: float = 0.05,
    keep_ratio: float = 0.20,
    max_workers: int | None = None,
    copy_workers: int | None = None,
) -> FastPipelineResult:
    """
    Run OpenCV + fast aesthetic scoring, then globally rank eligible images by Stage2 score.

    Images failing Stage1 or Stage2 go to ``AI_Trash``. Among eligible images, top
    ``best_ratio`` of the pool → ``AI_Best_fast``, next ``keep_ratio`` → ``AI_Keep_fast``, remainder
    of the pool → ``AI_Trash``.

    Args:
        source_dir: Directory containing input images (same layout as main pipeline ``source_dir``).
        config_path: YAML path; defaults to ``ConfigLoader.DEFAULT_CONFIG_PATH``.
        best_ratio: Fraction of the *eligible pool* for ``AI_Best_fast`` (e.g. 0.05 = top 5%).
        keep_ratio: Fraction of the *eligible pool* for ``AI_Keep_fast`` (after best bucket).
        max_workers: Parallel workers for Stage1+2; default ``min(32, os.cpu_count() or 4)``.
        copy_workers: Threads for file copies; defaults to ``max_workers``.

    Returns:
        :class:`FastPipelineResult` with path to ``analysis_fast.json`` and summary stats.
    """
    src = Path(source_dir).resolve()
    cfg_path = config_path or ConfigLoader.DEFAULT_CONFIG_PATH
    config = _load_config_merged(cfg_path, str(src))
    folders = _fast_folder_paths(src)
    _ensure_output_dirs(folders)

    images = _list_image_files(src)
    total = len(images)
    if total == 0:
        logger.warning("fast_pipeline: no images under %s", src)
        payload = {
            "pipeline": "fast_stage1_stage2",
            "source_dir": str(src),
            "config_path": str(Path(cfg_path).resolve()),
            "folders": {k: str(v) for k, v in folders.items()},
            "parameters": {
                "best_ratio": best_ratio,
                "keep_ratio": keep_ratio,
                "max_workers": max_workers,
            },
            "timing": {
                "wall_seconds_total": 0.0,
                "parallel_stage12_wall_seconds": 0.0,
                "rank_and_copy_wall_seconds": 0.0,
            },
            "aggregate_stage_times": {
                "sum_stage1_seconds": 0.0,
                "sum_stage2_seconds": 0.0,
                "avg_stage1_seconds_per_image": None,
                "avg_stage2_seconds_per_image": None,
            },
            "throughput_images_per_second_total": None,
            "counts": {"input": 0},
            "images": [],
        }
        out_path = src / "analysis_fast.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)
        return FastPipelineResult(
            analysis_path=out_path,
            folders=folders,
            total_images=0,
            wall_seconds=0.0,
            throughput_ips=0.0,
            summary=payload,
        )

    mw = max_workers if max_workers is not None else min(32, (os.cpu_count() or 4))
    mw = max(1, int(mw))

    logger.info(
        "fast_pipeline start: %s images, workers=%s, best_ratio=%s keep_ratio=%s → %s",
        total,
        mw,
        best_ratio,
        keep_ratio,
        src,
    )

    t_total0 = time.perf_counter()

    rows: List[_WorkerRow] = []
    t_collect0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=mw) as ex:
        futures = {ex.submit(_process_one_image, file_path=str(p), config=config): p for p in images}
        for fut in as_completed(futures):
            rows.append(fut.result())
    t_collect1 = time.perf_counter()
    parallel_wall = t_collect1 - t_collect0

    sum_s1 = sum(r.stage1_seconds for r in rows)
    sum_s2 = sum(r.stage2_seconds for r in rows)
    avg_s1 = sum_s1 / total if total else None
    avg_s2 = sum_s2 / total if total else None

    eligible = [r for r in rows if r.status == "eligible"]
    rejects = [r for r in rows if r.status != "eligible"]

    best_rows, keep_rows, eligible_trash, pool_rank_by_name = _bucket_eligible(
        eligible,
        best_ratio=best_ratio,
        keep_ratio=keep_ratio,
    )

    t_rank0 = time.perf_counter()
    copy_w = copy_workers if copy_workers is not None else mw
    copy_w = max(1, int(copy_w))

    file_lock = Lock()
    copy_tasks: List[tuple[str, Path, str]] = []

    def _add_copy(src_path: str, dest: Path, classification: str) -> None:
        copy_tasks.append((src_path, dest, classification))

    for r in best_rows:
        _add_copy(r.path, folders["best"], "best_fast")
    for r in keep_rows:
        _add_copy(r.path, folders["keep"], "keep_fast")
    for r in eligible_trash:
        _add_copy(r.path, folders["trash"], "trash_eligible")
    for r in rejects:
        _add_copy(r.path, folders["trash"], "trash_reject")

    waiters = []
    with ThreadPoolExecutor(max_workers=copy_w) as ex:
        waiters = [ex.submit(_copy_one, sp, dest, file_lock) for sp, dest, _ in copy_tasks]
    for w in waiters:
        w.result()
    t_rank1 = time.perf_counter()
    rank_and_copy_wall = t_rank1 - t_rank0

    t_total1 = time.perf_counter()
    wall_total = t_total1 - t_total0
    throughput = (total / wall_total) if wall_total > 0 else 0.0

    image_payload: List[Dict[str, Any]] = []
    for r in best_rows:
        image_payload.append(
            _ai_record_for_row(r, "AI_Best_fast", pool_rank_by_name.get(r.file_name))
        )
    for r in keep_rows:
        image_payload.append(
            _ai_record_for_row(r, "AI_Keep_fast", pool_rank_by_name.get(r.file_name))
        )
    for r in eligible_trash:
        image_payload.append(
            _ai_record_for_row(r, "AI_Trash", pool_rank_by_name.get(r.file_name))
        )
    for r in rejects:
        image_payload.append(_ai_record_for_row(r, "AI_Trash", None))

    # Stable order by file name for diff-friendly output
    image_payload.sort(key=lambda d: str(d.get("file_name", "")))

    payload: Dict[str, Any] = {
        "pipeline": "fast_stage1_stage2",
        "source_dir": str(src),
        "config_path": str(Path(cfg_path).resolve()),
        "folders": {k: str(v) for k, v in folders.items()},
        "parameters": {
            "best_ratio": best_ratio,
            "keep_ratio": keep_ratio,
            "max_workers_stage12": mw,
            "copy_workers": copy_w,
        },
        "timing": {
            "wall_seconds_total": round(wall_total, 4),
            "parallel_stage12_wall_seconds": round(parallel_wall, 4),
            "rank_and_copy_wall_seconds": round(rank_and_copy_wall, 4),
        },
        "aggregate_stage_times": {
            "sum_stage1_seconds": round(sum_s1, 4),
            "sum_stage2_seconds": round(sum_s2, 4),
            "avg_stage1_seconds_per_image": None if avg_s1 is None else round(avg_s1, 6),
            "avg_stage2_seconds_per_image": None if avg_s2 is None else round(avg_s2, 6),
        },
        "throughput_images_per_second_total": round(throughput, 4),
        "counts": {
            "input": total,
            "eligible": len(eligible),
            "ai_best_fast": len(best_rows),
            "ai_keep_fast": len(keep_rows),
            "ai_trash": len(eligible_trash) + len(rejects),
            "stage1_reject": sum(1 for r in rows if r.status == "stage1_reject"),
            "stage2_threshold_reject": sum(
                1 for r in rows if r.status == "stage2_threshold_reject"
            ),
            "stage2_prefilter_reject": sum(
                1 for r in rows if r.status == "stage2_prefilter_reject"
            ),
            "failed": sum(1 for r in rows if r.status == "failed"),
        },
        "images": image_payload,
    }

    out_path = src / "analysis_fast.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(json_safe(payload), f, ensure_ascii=False, indent=2)

    logger.info(
        "fast_pipeline done: %s images in %.2fs (%.2f img/s); parallel S12=%.2fs; "
        "best=%s keep=%s trash=%s → %s",
        total,
        wall_total,
        throughput,
        parallel_wall,
        payload["counts"]["ai_best_fast"],
        payload["counts"]["ai_keep_fast"],
        payload["counts"]["ai_trash"],
        out_path,
    )
    logger.info(
        "fast_pipeline timings: wall_total=%.4fs parallel_stage12=%.4fs rank_copy=%.4fs "
        "avg_stage1=%.6fs avg_stage2=%.6fs sum_stage1=%.4fs sum_stage2=%.4fs",
        wall_total,
        parallel_wall,
        rank_and_copy_wall,
        avg_s1 or 0.0,
        avg_s2 or 0.0,
        sum_s1,
        sum_s2,
    )

    return FastPipelineResult(
        analysis_path=out_path,
        folders=folders,
        total_images=total,
        wall_seconds=wall_total,
        throughput_ips=throughput,
        summary=payload,
    )


__all__ = ["FastPipelineResult", "run_fast_pipeline"]
