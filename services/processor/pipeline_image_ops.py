"""
Shared per-image helpers for aesthetic pipeline and stage-aware PIPELINE_STAGE jobs.

Monolithic ``AestheticPipeline`` and staged runners call the same primitives so scoring and
audit lines stay aligned.
"""
from __future__ import annotations

import json
import math
import shutil
import statistics
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from utils.config_loader import ConfigLoader
from utils.json_safe import json_safe
from utils.stage3_result import (
    apply_blended_score_to_stage3_result,
    assert_stage3_result_consistent,
    attach_stage3_result,
    full_stage3_result,
    reconcile_stage3_result_from_legacy,
)
from engine.operators.image_processor import ImageProcessor

# Stage3 admission presets when ``processing.pipeline_mode`` is set (fast | balanced | strict).
# Omit ``pipeline_mode`` in YAML to keep using ``stage3_gating`` thresholds only (legacy).
# ``delivery`` uses ``delivery_mode`` (see :func:`apply_delivery_mode_overrides`).
STAGE3_PIPELINE_MODE_CONFIG: Dict[str, Dict[str, float]] = {
    "fast": {"top_k_ratio": 0.1, "threshold": 0.7},
    "balanced": {"top_k_ratio": 0.12, "threshold": 0.66},
    "strict": {"top_k_ratio": 0.25, "threshold": 0.6},
}


def is_delivery_pipeline_mode(config: Mapping[str, Any]) -> bool:
    raw = (config.get("processing") or {}).get("pipeline_mode")
    return str(raw or "").strip().lower() == "delivery"


def apply_delivery_mode_overrides(config: Dict[str, Any]) -> None:
    """
    When ``processing.pipeline_mode`` is ``delivery``, merge ``delivery_mode`` into
    ``stage3`` and mark quiet logging. Opt-in only; no-op for other modes.
    """
    if not is_delivery_pipeline_mode(config):
        return
    dm = dict(config.get("delivery_mode") or {})
    proc = config.setdefault("processing", {})
    proc["delivery_quiet_logs"] = bool(dm.get("quiet_logs", True))

    s3 = config.setdefault("stage3", {})
    if "full_top_k" in dm:
        s3["full_analysis_top_k"] = max(0, int(dm["full_top_k"]))
    if dm.get("stage3_fast_only", True):
        s3["strategy"] = "fast_first"
    fnp = dm.get("fast_num_predict")
    if fnp is not None:
        s3["fast_num_predict"] = max(64, int(fnp))


def resolve_stage3_gating_params(
    config: Dict[str, Any],
) -> tuple[float | None, float | None, Dict[str, Any]]:
    """
    Return ``(stage3_threshold, top_k_ratio, meta)`` for Stage3 admission.

    If ``processing.pipeline_mode`` is set (non-empty), values come from
    :data:`STAGE3_PIPELINE_MODE_CONFIG`, except ``delivery`` which reads ``delivery_mode``
    (unknown non-delivery mode → ``balanced``).

    If ``pipeline_mode`` is absent, legacy ``processing.stage3_gating`` YAML values are used.
    """
    proc = config.get("processing") or {}
    sg = dict(proc.get("stage3_gating") or {})
    raw_mode = proc.get("pipeline_mode")
    meta: Dict[str, Any] = {"gating_source": "yaml_legacy", "pipeline_mode": None}

    if raw_mode is None or (isinstance(raw_mode, str) and not str(raw_mode).strip()):
        thresh = sg.get("stage3_threshold")
        ratio = sg.get("top_k_ratio")
        if thresh is not None:
            meta["stage3_threshold"] = float(thresh)
        if ratio is not None:
            meta["top_k_ratio"] = float(ratio)
        return (
            float(thresh) if thresh is not None else None,
            float(ratio) if ratio is not None else None,
            meta,
        )

    mode = str(raw_mode).strip().lower()
    if mode == "delivery":
        dm = dict(config.get("delivery_mode") or {})
        thresh_raw = dm.get("stage3_normalized_threshold", 0.72)
        ratio_raw = dm.get("stage2_target_ratio", 0.12)
        try:
            thresh_f = float(thresh_raw)
        except (TypeError, ValueError):
            thresh_f = 0.72
        try:
            ratio_f = float(ratio_raw)
        except (TypeError, ValueError):
            ratio_f = 0.12
        thresh_f = max(0.0, min(1.0, thresh_f))
        ratio_f = max(0.0, ratio_f)
        ratio_out: float | None = None if ratio_f <= 0 else ratio_f
        meta = {
            "gating_source": "delivery_mode",
            "pipeline_mode": mode,
            "stage3_threshold": thresh_f,
            "top_k_ratio": ratio_f,
        }
        return thresh_f, ratio_out, meta

    if mode not in STAGE3_PIPELINE_MODE_CONFIG:
        mode = "balanced"
    preset = STAGE3_PIPELINE_MODE_CONFIG[mode]
    thresh_f = float(preset["threshold"])
    ratio_f = float(preset["top_k_ratio"])
    meta = {
        "gating_source": "pipeline_mode",
        "pipeline_mode": mode,
        "stage3_threshold": thresh_f,
        "top_k_ratio": ratio_f,
    }
    return thresh_f, ratio_f, meta


def bootstrap_pipeline_layout(config_path: str, source_dir: str) -> Tuple[Dict[str, Any], Path, Dict[str, Path], Dict[str, Path]]:
    """
    Load YAML config, set ``source_dir``, create best/keep/trash and log parent dirs.
    Shared by monolithic pipeline and stage jobs.
    """
    config = ConfigLoader.load(config_path)
    config["paths"]["source_dir"] = source_dir
    src = Path(config["paths"]["source_dir"])
    folders = ConfigLoader.get_folder_paths(config, src)
    log_paths = ConfigLoader.get_log_paths(config, src)
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    for log_path in log_paths.values():
        log_path.parent.mkdir(parents=True, exist_ok=True)
    apply_delivery_mode_overrides(config)
    return config, src, folders, log_paths


def append_aesthetic_audit_line(
    *,
    config: Dict[str, Any],
    folders: Dict[str, Path],
    log_paths: Dict[str, Path],
    file_lock: Lock | None,
    image_path: str,
    ai_data: Dict[str, Any],
) -> None:
    """
    Append one JSON line to the aesthetic audit log and copy the source image into
    best / keep / trash based on ``ai_data[\"score\"]`` (same semantics as ``AestheticPipeline._process_action``).
    """
    score = ai_data.get("score", 0)
    file_name = Path(image_path).name

    classification_cfg = ConfigLoader.get_classification_thresholds(config)
    if score >= classification_cfg["best_threshold"]:
        target_folder = folders["best"]
    elif score >= classification_cfg["keep_threshold"]:
        target_folder = folders["keep"]
    else:
        target_folder = folders["trash"]

    dest_path = target_folder / file_name

    def _write() -> None:
        shutil.copy2(image_path, dest_path)

        log_entry: Dict[str, Any] = {
            "image": file_name,
            "file_name": file_name,
            "score": round(float(score), 1) if score is not None else 0,
            "overall_score": round(float(score), 1) if score is not None else 0,
            "reason": ai_data.get("reason", ""),
            "tags": ai_data.get("tags", []),
            "dimensions": ai_data.get("dimensions", {}),
            "weakness": ai_data.get("weakness", ""),
        }
        dc = ai_data.get("dimension_comments")
        if dc:
            log_entry["dimension_comments"] = dc
        dr = ai_data.get("dimensions_raw")
        if dr:
            log_entry["dimensions_raw"] = dr
        s3p = ai_data.get("stage3_postprocess")
        if s3p:
            log_entry["stage3_postprocess"] = s3p
        s3m = ai_data.get("stage3_meta")
        if s3m:
            log_entry["stage3_meta"] = s3m
        es = ai_data.get("editing_suggestions")
        if es:
            log_entry["editing_suggestions"] = es
        rb = ai_data.get("reason_bilingual")
        if rb and isinstance(rb, dict):
            log_entry["reason_bilingual"] = rb
        wb = ai_data.get("weakness_bilingual")
        if wb and isinstance(wb, dict):
            log_entry["weakness_bilingual"] = wb
        verdict = ai_data.get("verdict")
        if verdict:
            log_entry["verdict"] = verdict
        fa = ai_data.get("full_analysis")
        if fa:
            log_entry["full_analysis"] = fa
        sr = ai_data.get("stage3_result")
        if sr:
            log_entry["stage3_result"] = sr
        di = ai_data.get("debug_info")
        if di is not None:
            log_entry["debug_info"] = di
        if di:
            if di.get("orientation"):
                log_entry["orientation"] = di["orientation"]
            if di.get("width") is not None:
                log_entry["width"] = di["width"]
            if di.get("height") is not None:
                log_entry["height"] = di["height"]

        log_file = log_paths["log_file"]
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(json_safe(log_entry), ensure_ascii=False) + "\n")

    if file_lock is not None:
        with file_lock:
            _write()
    else:
        _write()


def assess_stage1_opencv(
    config: Dict[str, Any],
    file_path: str,
) -> Tuple[bool, str, float, Dict[str, Any]]:
    """OpenCV gate (blur / exposure / contrast). Returns (passed, reason, tech_score, debug_info)."""
    quality_cfg = ConfigLoader.get_quality_thresholds(config)
    passes_quality, reason, tech_score, debug_info = ImageProcessor.assess_image_quality(
        file_path,
        quality_cfg,
    )
    return passes_quality, str(reason), float(tech_score), dict(debug_info or {})


def fast_aesthetic_score(file_path: str) -> float:
    return float(ImageProcessor.fast_aesthetic_assessment(file_path))


def passes_stage2_thresholds(config: Dict[str, Any], tech_score: float, fast_score: float) -> bool:
    fast_cfg = ConfigLoader.get_fast_aesthetic_thresholds(config)
    return tech_score >= fast_cfg["tech_score_min"] and fast_score >= fast_cfg["fast_aesthetic_score_min"]


def stage2_normalized_score(tech_score: float, fast_score: float) -> float:
    """Stage2 combined score in [0, 1]; matches Stage 2 reject weighting."""
    combined = float(tech_score) * 0.6 + float(fast_score) * 0.4
    return max(0.0, min(1.0, combined / 100.0))


def stage3_gating_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    return dict(config.get("processing", {}).get("stage3_gating") or {})


def _percentile_linear(sorted_vals: Sequence[float], pct: float) -> float:
    """``pct`` in [0, 100]; linear interpolation on sorted values."""
    xs = list(sorted_vals)
    n = len(xs)
    if n == 0:
        return 0.0
    if n == 1:
        return float(xs[0])
    p = max(0.0, min(100.0, float(pct)))
    rank = (p / 100.0) * (n - 1)
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return float(xs[lo])
    w = rank - lo
    return float(xs[lo] * (1.0 - w) + xs[hi] * w)


def _batch_top_k_scale(batch_n: int) -> float:
    """Scale down top-k fraction on large jobs (more aggressive Stage3 admission)."""
    if batch_n > 1000:
        return 0.48
    if batch_n > 500:
        return 0.68
    return 1.0


def _dynamic_percentile_floor(batch_n: int) -> float:
    """Percentile of the Stage2-norm cohort used as a candidate floor (0–100)."""
    if batch_n > 1000:
        return 80.0
    if batch_n > 500:
        return 74.0
    return 68.0


def _effective_stage3_threshold(
    scores: Sequence[float],
    base_t: float,
    *,
    batch_n: int,
    sg: Mapping[str, Any],
) -> tuple[float, Dict[str, Any]]:
    """
    Raise the floor using distribution (percentile + mean/std), never below ``base_t``.
    """
    if not scores:
        return float(base_t), {"score_count": 0}
    xs = sorted(float(s) for s in scores)
    mean = float(statistics.mean(xs))
    std = float(statistics.pstdev(xs)) if len(xs) > 1 else 0.0
    pct_floor = float(sg.get("dynamic_percentile_floor", _dynamic_percentile_floor(batch_n)))
    pct_floor = max(0.0, min(99.5, pct_floor))
    t_pct = _percentile_linear(xs, pct_floor)
    z = float(sg.get("dynamic_z_sigma", 0.28) or 0.28)
    t_stat = mean + z * std
    cap = float(sg.get("dynamic_threshold_cap", 0.93) or 0.93)
    t_stat_capped = min(cap, t_stat)
    t_eff = max(float(base_t), t_pct, t_stat_capped)
    t_eff = max(float(base_t), min(cap, t_eff))
    meta = {
        "score_count": len(xs),
        "norm_mean": round(mean, 5),
        "norm_std": round(std, 5),
        "norm_pctl_floor": round(pct_floor, 2),
        "norm_percentile_value": round(t_pct, 5),
        "norm_stat_floor": round(t_stat_capped, 5),
    }
    return t_eff, meta


def _stage3_estimated_seconds_per_image(config: Mapping[str, Any]) -> float:
    sg = dict((config.get("processing") or {}).get("stage3_gating") or {})
    raw = sg.get("estimated_vlm_seconds_per_image")
    if raw is not None:
        try:
            return max(1.0, float(raw))
        except (TypeError, ValueError):
            pass
    return 22.0


def apply_stage3_candidates_gating(
    eligible_rows: Sequence[Dict[str, Any]],
    *,
    config: Dict[str, Any],
    batch_input_scale_n: int | None = None,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], Dict[str, Any]]:
    """
    Optional Stage3 admission after Stage 2 passes: ``stage3_threshold`` (0–1 normalized)
    then optional ``top_k_ratio`` slice on the remaining pool (sorted best-first).

    When ``processing.pipeline_mode`` is set, thresholds come from mode presets; otherwise
    YAML ``stage3_gating`` is used (legacy).

    Dynamic batch gating (``processing.stage3_gating.dynamic_batch_gating`` default ``true``):
    scales ``top_k_ratio`` by input job size, tightens threshold using score distribution,
    and logs inferred GPU savings vs admitting the full Stage2 pool.

    Returns ``(kept_rows, skipped_rows, diagnostics)``.
    """
    thresh, ratio, gating_meta = resolve_stage3_gating_params(config)
    sg = stage3_gating_settings(config)
    if str(gating_meta.get("gating_source") or "") == "delivery_mode":
        dyn_on = bool(sg.get("dynamic_batch_gating", False))
    else:
        dyn_on = bool(sg.get("dynamic_batch_gating", True))

    before = len(eligible_rows)
    if thresh is None and ratio is None:
        return (
            [dict(r) for r in eligible_rows],
            [],
            {"before": before, "after": before, "skipped": 0, **gating_meta},
        )

    batch_n = int(batch_input_scale_n) if batch_input_scale_n is not None else before
    batch_n = max(batch_n, before)

    enriched: list[Dict[str, Any]] = []
    for r in eligible_rows:
        d = dict(r)
        ts = float(d["tech_score"])
        fs = float(d["fast_score"])
        d["_stage2_norm"] = stage2_normalized_score(ts, fs)
        enriched.append(d)

    scores = [float(r["_stage2_norm"]) for r in enriched]
    base_t = float(thresh) if thresh is not None else 0.0
    t_eff, dist_meta = (
        _effective_stage3_threshold(scores, base_t, batch_n=batch_n, sg=sg)
        if (thresh is not None and dyn_on)
        else (base_t, {"score_count": len(scores)})
    )
    if thresh is not None and not dyn_on:
        t_eff = base_t

    admission_pct: float | None = None
    if thresh is not None and scores:
        admission_pct = 100.0 * (sum(1 for s in scores if s <= t_eff) / float(len(scores)))

    base_r = float(ratio) if ratio is not None else 1.0
    r_eff = base_r
    if ratio is not None and dyn_on:
        r_eff = min(0.95, max(0.02, base_r * _batch_top_k_scale(batch_n)))

    pool = enriched
    if thresh is not None:
        pool = [r for r in pool if float(r["_stage2_norm"]) >= t_eff]

    pool.sort(key=lambda x: float(x["_stage2_norm"]), reverse=True)

    if ratio is not None:
        rat = float(r_eff)
        if rat <= 0:
            kept = []
        elif rat >= 1.0:
            kept = list(pool)
        elif not pool:
            kept = []
        else:
            keep_n = int(math.floor(len(pool) * rat))
            kept = pool[:keep_n]
    else:
        kept = list(pool)

    cap_raw = sg.get("max_candidates")
    if cap_raw is not None:
        try:
            cap_n = int(cap_raw)
            if cap_n >= 0:
                kept = kept[:cap_n]
        except (TypeError, ValueError):
            pass

    kept_names = {str(r["file_name"]) for r in kept}
    skipped = [r for r in enriched if str(r["file_name"]) not in kept_names]

    for r in kept + skipped:
        r.pop("_stage2_norm", None)

    after = len(kept)
    skipped_n = before - after
    est_sec = _stage3_estimated_seconds_per_image(config)
    saved_inf = max(0, before - after)
    diag: Dict[str, Any] = {
        "before": before,
        "after": after,
        "skipped": skipped_n,
        "batch_input_scale_n": batch_n,
        "stage3_threshold_effective": round(t_eff, 5) if thresh is not None else None,
        "top_k_ratio_effective": round(r_eff, 5) if ratio is not None else None,
        "dynamic_batch_gating": dyn_on,
        "admission_percentile": round(admission_pct, 3) if admission_pct is not None else None,
        "stage3_inferences_saved": int(saved_inf),
        "estimated_gpu_seconds_saved": round(float(saved_inf) * est_sec, 2),
        "estimated_vlm_seconds_per_image": est_sec,
        "distribution": dist_meta,
        **gating_meta,
    }
    return kept, skipped, diag


def fake_result_stage1_reject(
    *,
    tech_score: float,
    reason: str,
    debug_info: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "score": tech_score,
        "reason": f"Technical issue: {reason}",
        "tags": ["technical_issue"],
        "dimensions": {},
        "weakness": reason,
        "debug_info": debug_info,
    }


def fake_result_stage2_reject(
    *,
    tech_score: float,
    fast_score: float,
    debug_info: Dict[str, Any],
    reject_detail: str | None = None,
) -> Dict[str, Any]:
    combined_score = tech_score * 0.6 + fast_score * 0.4
    weakness = f"Tech {tech_score:.0f} + Aesthetic {fast_score:.0f}"
    tags: List[str] = ["low_quality"]
    if reject_detail:
        weakness = f"{weakness} | {reject_detail}"
        if str(reject_detail).startswith("stage2_"):
            tags.append("stage2_prefilter")
    return {
        "score": combined_score,
        "reason": "Low quality score" if not reject_detail else f"Stage 2 filter: {reject_detail}",
        "tags": tags,
        "dimensions": {},
        "weakness": weakness,
        "debug_info": debug_info,
    }


def fake_result_stage2_dedupe_skip(
    *,
    tech_score: float,
    fast_score: float,
    debug_info: Dict[str, Any],
) -> Dict[str, Any]:
    combined_score = tech_score * 0.6 + fast_score * 0.4
    norm = stage2_normalized_score(tech_score, fast_score)
    return {
        "score": round(combined_score, 1),
        "reason": "Near-duplicate frame (phash); VLM skipped",
        "tags": ["near_duplicate", "stage2_dedup"],
        "dimensions": {},
        "weakness": "Burst / near-duplicate suppressed before Stage 3",
        "debug_info": {**dict(debug_info or {}), "stage2_normalized": norm},
        "stage3_meta": {
            "outcome": "skipped_near_duplicate",
            "prompt_profile": "none",
            "latency_ms": 0.0,
        },
    }


def fake_result_stage3_gated_skip(
    *,
    tech_score: float,
    fast_score: float,
    debug_info: Dict[str, Any],
) -> Dict[str, Any]:
    combined_score = tech_score * 0.6 + fast_score * 0.4
    norm = stage2_normalized_score(tech_score, fast_score)
    return {
        "score": round(combined_score, 1),
        "reason": "Stage3 skipped (Stage2 gating); heuristic score only",
        "tags": ["stage3_skipped_gating"],
        "dimensions": {},
        "weakness": "No VLM (stage3 gating)",
        "debug_info": {**dict(debug_info or {}), "stage2_normalized": norm},
        "stage3_meta": {
            "outcome": "skipped_stage3_gating",
            "prompt_profile": "none",
            "latency_ms": 0.0,
        },
    }


def fake_result_stage3_vlm_fallback(
    *,
    tech_score: float,
    reason_txt: str,
    debug_info: Dict[str, Any],
) -> Dict[str, Any]:
    reason_txt = (reason_txt or "unknown")[:200]
    fallback_score = max(0.0, min(100.0, tech_score * 0.35 + 12.0))
    fallback_score = min(fallback_score, 52.0)
    return {
        "score": round(fallback_score, 1),
        "reason": f"VLM 不可用，已用技术分回退: {reason_txt}",
        "tags": ["vlm_error"],
        "dimensions": {},
        "weakness": reason_txt,
        "debug_info": debug_info,
    }


def merge_vlm_and_technical_scores(
    config: Dict[str, Any],
    vlm_result: Dict[str, Any],
    tech_score: float,
    debug_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Apply technical + AI weights to Stage 3 output (mutates a copy of ``vlm_result``)."""
    out = dict(vlm_result)
    reconcile_stage3_result_from_legacy(out)
    technical_weight = float(config["evaluation"]["technical_weight"])
    ai_weight = float(config["evaluation"]["ai_weight"])
    ai_part = float(out["score"])
    final_score = ai_part * ai_weight + float(tech_score) * technical_weight
    final_score = max(0.0, min(100.0, final_score))
    out["score"] = round(final_score, 1)
    apply_blended_score_to_stage3_result(out, float(out["score"]))
    out["debug_info"] = debug_info
    assert_stage3_result_consistent(out)
    return out


def finalize_stage3_dual_result(
    *,
    config: Dict[str, Any],
    tech_score: float,
    debug_info: Dict[str, Any],
    fast_inner: Dict[str, Any] | None,
    full_inner: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """
    Merge fast / full Stage3 VLM payloads (pre-``merge_vlm``) into one audit-ready dict.
    When ``full_inner`` succeeds, it drives dimensions, highlight/gap, edits; optional ``fast_inner``
    verdict is preserved under ``full_analysis.fast_verdict_bilingual``.
    """
    if full_inner and not full_inner.get("error"):
        out = merge_vlm_and_technical_scores(config, full_inner, tech_score, debug_info)
        fa: Dict[str, Any] = {}
        for k in (
            "dimensions",
            "dimensions_raw",
            "weakness",
            "weakness_bilingual",
            "dimension_comments",
            "editing_suggestions",
            "stage3_postprocess",
        ):
            v = full_inner.get(k)
            if v:
                fa[k] = v
        if fast_inner and not fast_inner.get("error"):
            fv = fast_inner.get("verdict_bilingual") or fast_inner.get("reason_bilingual")
            if isinstance(fv, dict) and (fv.get("zh") or fv.get("en")):
                fa["fast_verdict_bilingual"] = fv
        out["full_analysis"] = fa if fa else None
        sm = dict(out.get("stage3_meta") or {})
        sm["stage3_mode"] = "fast_then_full"
        if fast_inner and fast_inner.get("stage3_meta"):
            sm["fast_stage3_meta"] = fast_inner["stage3_meta"]
        out["stage3_meta"] = sm
        verdict = (out.get("reason") or "").strip()
        if verdict:
            out["verdict"] = verdict
        outcome = str((full_inner.get("stage3_meta") or {}).get("outcome") or "")
        degraded = bool(out.get("inference_degraded")) or outcome == "degraded_inference"
        fb = outcome == "fallback_defaults"
        fr = full_stage3_result(
            score=float(out["score"]),
            verdict=str(out.get("verdict") or out.get("reason") or ""),
            dimensions_cal=out.get("dimensions") or full_inner.get("dimensions") or {},
            inference_degraded=degraded,
            used_fallback_defaults=fb,
        )
        attach_stage3_result(out, fr)
        assert_stage3_result_consistent(out)
        return out

    assert fast_inner is not None
    out = merge_vlm_and_technical_scores(config, fast_inner, tech_score, debug_info)
    sm = dict(out.get("stage3_meta") or {})
    sm["stage3_mode"] = "fast_only"
    out["stage3_meta"] = sm
    v = (out.get("verdict") or out.get("reason") or "").strip()
    if v:
        out["verdict"] = v
    return out
