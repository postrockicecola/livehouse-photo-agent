"""Stage4 editing VLM pass (after Stage3 dimensional scoring)."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Mapping

from inference.parsers import clean_json_response, parse_editing_suggestions_response
from inference.types import inference_status_ok
from services.edit_adjustments import parse_edit_adjustments_response
from services.processor.stages.stage4_editing_prompt_builder import (
    STAGE4_NUMERIC_PROMPT_VERSION,
    STAGE4_PROMPT_VERSION,
    build_stage4_editing_prompt,
    build_stage4_numeric_prompt,
)
from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)


def stage4_editing_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(config.get("stage4_editing") or {})
    mode = str(raw.get("mode", "numeric") or "numeric").strip().lower()
    if mode not in ("numeric", "text"):
        mode = "numeric"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "num_predict": max(128, int(raw.get("num_predict", 384) or 384)),
        "min_score": float(raw.get("min_score", 0) or 0),
        "skip_on_fallback_defaults": bool(raw.get("skip_on_fallback_defaults", True)),
        "skip_on_cache_hit": bool(raw.get("skip_on_cache_hit", True)),
    }


def _dimension_summary(dimensions: Mapping[str, Any] | None) -> str:
    if not dimensions:
        return ""
    parts: list[str] = []
    for k in STAGE3_DIM_KEYS:
        v = dimensions.get(k)
        if v is None:
            continue
        try:
            parts.append(f"{k}={float(v):.1f}")
        except (TypeError, ValueError):
            continue
    return ", ".join(parts)


def maybe_run_stage4_editing(
    *,
    inference_client: Any,
    config: Mapping[str, Any],
    image_path: str,
    stage3_success: Mapping[str, Any],
    queue_priority: int,
    log_context: dict[str, Any] | None,
    inference_extra_metadata: dict[str, Any] | None,
    infer_acc: dict[str, float] | None,
    used_fallback_defaults: bool,
) -> tuple[list[dict[str, str]], dict[str, float] | None, dict[str, Any]]:
    """
    Optional follow-up VLM call for editing recommendations.

    In ``mode="numeric"`` returns machine-appliable grade params; in ``mode="text"``
    returns human-readable ``editing_suggestions``.

    Returns ``(suggestions, adjustments, stage4_meta)`` where ``adjustments`` is a
    clamped float dict (numeric mode) or ``None``.
    """
    cfg = stage4_editing_settings(config)
    numeric = cfg["mode"] == "numeric"
    meta: dict[str, Any] = {
        "prompt_version": STAGE4_NUMERIC_PROMPT_VERSION if numeric else STAGE4_PROMPT_VERSION,
        "mode": cfg["mode"],
        "outcome": "skipped",
    }
    if not cfg["enabled"]:
        meta["outcome"] = "disabled"
        return [], None, meta

    if used_fallback_defaults and cfg["skip_on_fallback_defaults"]:
        meta["outcome"] = "skipped_fallback_defaults"
        return [], None, meta

    score = float(stage3_success.get("score") or 0.0)
    if score < cfg["min_score"]:
        meta["outcome"] = "skipped_below_min_score"
        meta["min_score"] = cfg["min_score"]
        return [], None, meta

    wa = stage3_success.get("weakness_bilingual") or {}
    sa = stage3_success.get("reason_bilingual") or {}
    strongest_en = str(sa.get("en") or sa.get("zh") or stage3_success.get("reason") or "")
    weakest_en = str(wa.get("en") or wa.get("zh") or stage3_success.get("weakness") or "")

    dim_summary = _dimension_summary(stage3_success.get("dimensions"))
    if numeric:
        prompt = build_stage4_numeric_prompt(
            dimension_summary=dim_summary,
            strongest_en=strongest_en,
            weakest_en=weakest_en,
        )
    else:
        prompt = build_stage4_editing_prompt(
            dimension_summary=dim_summary,
            strongest_en=strongest_en,
            weakest_en=weakest_en,
        )

    from services.processor.stages.deep_analysis import _run_inference_full

    md = dict(inference_extra_metadata or {})
    md["stage4_editing"] = True
    md["num_predict"] = cfg["num_predict"]

    t0 = time.perf_counter()
    response = _run_inference_full(
        inference_client,
        image_path,
        prompt,
        queue_priority,
        log_context=log_context,
        inference_extra_metadata=md,
    )
    t1 = time.perf_counter()
    if infer_acc is not None and inference_status_ok(str(response.get("status") or "")):
        md_resp = response.get("metadata") or {}
        if isinstance(md_resp, dict):
            infer_acc["queue_wait_sec"] = infer_acc.get("queue_wait_sec", 0.0) + float(
                md_resp.get("queue_wait_sec") or 0.0
            )
        infer_acc["model_infer_sec"] = infer_acc.get("model_infer_sec", 0.0) + (t1 - t0)

    if not inference_status_ok(str(response.get("status") or "")):
        meta["outcome"] = "vlm_error"
        meta["error"] = str(response.get("error", ""))[:300]
        logger.warning(
            "Stage4 editing VLM failed image=%s err=%s",
            Path(image_path).name,
            meta["error"],
        )
        return [], None, meta

    raw = (response.get("text") or "").strip()
    clean = clean_json_response(raw)

    if numeric:
        adj = parse_edit_adjustments_response(clean)
        if not adj.is_active():
            meta["outcome"] = "parse_empty"
            logger.warning(
                "Stage4 numeric parse empty/inactive image=%s clean_len=%s",
                Path(image_path).name,
                len(clean),
            )
            return [], None, meta
        meta["outcome"] = "success"
        meta["latency_ms"] = round((t1 - t0) * 1000.0, 1)
        return [], adj.as_dict(), meta

    suggestions = parse_editing_suggestions_response(clean)
    if not suggestions:
        meta["outcome"] = "parse_empty"
        logger.warning(
            "Stage4 editing parse empty image=%s clean_len=%s",
            Path(image_path).name,
            len(clean),
        )
        return [], None, meta

    meta["outcome"] = "success"
    meta["count"] = len(suggestions)
    meta["latency_ms"] = round((t1 - t0) * 1000.0, 1)
    return suggestions, None, meta
