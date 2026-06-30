"""
Stage3 P0: dynamic weights + post-calibration (guardrails) using Stage1 signals.

Livehouse 设计动机
-----------------
VLM 维度分可能自相矛盾或与物理信号冲突（极糊却 focus 很高）。本模块在加权总分前：
1) 用 Stage1（拉普拉斯、边缘密度、高光占比等）对维度做有界修正；
2) 在 yaml 静态权重基础上做轻量场景重加权（高光/动感/对比等），并归一化。

仅依赖 dict 特征，无网络调用，便于单测与回放。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Tuple, TypedDict

from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)


class CalibrationMeta(TypedDict, total=False):
    caps: List[Dict[str, Any]]
    p0_version: str


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


def renormalize_weights(weights: Mapping[str, float], keys: Tuple[str, ...] = STAGE3_DIM_KEYS) -> Dict[str, float]:
    w = {k: max(0.0, _f(weights.get(k), 0.0)) for k in keys}
    s = sum(w.values())
    if s <= 0:
        n = len(keys)
        return {k: 1.0 / n for k in keys}
    return {k: w[k] / s for k in keys}


def apply_dynamic_weights(
    base_weights: Mapping[str, float],
    stage1: Optional[Mapping[str, Any]],
    blur_type: Optional[str],
) -> Dict[str, float]:
    """
    v1 规则：在静态权重上乘性调整后再归一化。

    - 高 highlight_frac → 略提高 exposure / 光色权重（舞台爆光常见，仍要评「控不控得住」）。
    - 高 shadow_frac → exposure + 轻微 noise。
    - 高对比 → composition / light_color 略升。
    - artistic / 有结构的 motion → moment + atmosphere 升，focus 降。
    - 高解析 + 有边 → deliverable 略升（更信「能交片」维度）。
    """
    w = {k: _f(base_weights.get(k), 0.0) for k in STAGE3_DIM_KEYS}
    w = renormalize_weights(w)

    if not stage1:
        return w

    bt = blur_type if blur_type is not None else stage1.get("blur_type")
    hf = _f(stage1.get("highlight_frac"))
    sf = _f(stage1.get("shadow_frac"))
    lap = _f(stage1.get("laplacian_var"))
    edges = _f(stage1.get("edge_ratio"))
    contrast = _f(stage1.get("contrast"))

    if hf >= 0.22:
        w["exposure_control"] *= 1.22
        w["light_color_character"] *= 1.10
    elif hf >= 0.12:
        w["exposure_control"] *= 1.12

    if sf >= 0.52:
        w["exposure_control"] *= 1.12
        w["noise_cleanliness"] *= 1.08
    elif sf >= 0.38:
        w["exposure_control"] *= 1.06

    if contrast >= 32.0:
        w["composition_framing"] *= 1.06
        w["light_color_character"] *= 1.05

    if bt == "artistic_motion_blur" or (bt in ("motion_blur", "slight_blur") and edges >= 0.004):
        w["moment_peak"] *= 1.14
        w["atmosphere_impact"] *= 1.12
        w["focus_sharpness"] *= 0.84
        w["deliverable_subject"] *= 0.96
    elif bt in ("motion_blur", "slight_blur", "focus_blur"):
        w["focus_sharpness"] *= 0.90
        w["moment_peak"] *= 1.06

    if lap >= 140.0 and edges >= 0.007:
        w["deliverable_subject"] *= 1.08

    out = renormalize_weights(w)
    logger.debug(
        "stage3 dynamic_weights: blur=%s hf=%.3f sf=%.3f lap=%.1f edges=%.4f -> eff_keys=%s",
        bt,
        hf,
        sf,
        lap,
        edges,
        {k: round(out[k], 4) for k in STAGE3_DIM_KEYS},
    )
    return out


def _clamp_score(x: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, x))


def calibrate_dimension_scores(
    raw_dimensions: Mapping[str, Any],
    stage1: Optional[Mapping[str, Any]],
    blur_type: Optional[str],
) -> Tuple[Dict[str, float], CalibrationMeta]:
    """
    Guardrail：用 Stage1 约束 VLM 各维，减少黑盒离谱分。

    返回 (校准后 8 维分, meta)。
    """
    out: Dict[str, float] = {}
    for k in STAGE3_DIM_KEYS:
        out[k] = _clamp_score(_f(raw_dimensions.get(k), 5.0), 0.0, 10.0)

    meta: CalibrationMeta = {"caps": [], "p0_version": "2026-04-p0"}

    if not stage1:
        return out, meta

    bt = blur_type if blur_type is not None else stage1.get("blur_type")
    lap = _f(stage1.get("laplacian_var"))
    edges = _f(stage1.get("edge_ratio"))
    hf = _f(stage1.get("highlight_frac"))
    contrast = _f(stage1.get("contrast"))

    # --- focus_sharpness：与拉普拉斯/边缘一致 ---
    if lap > 0:
        if bt == "artistic_motion_blur" and edges >= 0.0035:
            soft_cap = min(10.0, 5.2 + min(4.5, lap / 42.0) + min(1.2, edges * 120.0))
        elif edges >= 0.006 and lap >= 45:
            soft_cap = min(10.0, 4.8 + lap / 35.0)
        else:
            soft_cap = min(10.0, 3.6 + lap / 28.0 + min(2.0, edges * 140.0))

        if out["focus_sharpness"] > soft_cap:
            meta["caps"].append(
                {"dimension": "focus_sharpness", "before": out["focus_sharpness"], "cap": soft_cap, "reason": "stage1_lap_edges"}
            )
            out["focus_sharpness"] = soft_cap

    # --- exposure_control：极端高光场景压低「曝光很好」的幻觉 ---
    if hf >= 0.30 and out["exposure_control"] > 6.8:
        cap_e = 6.2 + max(0.0, (0.42 - hf) * 8.0)
        cap_e = _clamp_score(cap_e, 0.0, 10.0)
        if out["exposure_control"] > cap_e:
            meta["caps"].append(
                {"dimension": "exposure_control", "before": out["exposure_control"], "cap": cap_e, "reason": "stage1_highlight_frac"}
            )
            out["exposure_control"] = cap_e

    # --- noise_cleanliness：极低对比且边弱时，噪声维不宜打满 ---
    if contrast < 7.0 and edges < 0.0045 and out["noise_cleanliness"] > 7.2:
        cap_n = 6.0 + min(1.5, contrast / 10.0) + min(1.0, edges * 80.0)
        if out["noise_cleanliness"] > cap_n:
            meta["caps"].append(
                {"dimension": "noise_cleanliness", "before": out["noise_cleanliness"], "cap": cap_n, "reason": "stage1_low_contrast_edges"}
            )
            out["noise_cleanliness"] = cap_n

    # --- deliverable_subject：几乎无结构时，限制「交付满分」---
    if edges < 0.0028 and out["deliverable_subject"] > 6.5:
        cap_d = 4.5 + min(2.5, edges * 600.0)
        if out["deliverable_subject"] > cap_d:
            meta["caps"].append(
                {"dimension": "deliverable_subject", "before": out["deliverable_subject"], "cap": cap_d, "reason": "stage1_sparse_edges"}
            )
            out["deliverable_subject"] = cap_d

    for k in STAGE3_DIM_KEYS:
        out[k] = _clamp_score(out[k], 0.0, 10.0)

    return out, meta


def weighted_ai_score(dimensions: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """0–100 聚合分（与 deep_analysis 原逻辑一致：dim/10*100*weight）。"""
    total = 0.0
    for dim in STAGE3_DIM_KEYS:
        w = _f(weights.get(dim), 0.0)
        score = _f(dimensions.get(dim), 5.0)
        total += (score / 10.0) * 100.0 * w
    return max(0.0, min(100.0, total))


def normalize_raw_dimensions(raw: Mapping[str, Any]) -> Dict[str, float]:
    """确保 8 维齐全且落在 [0,10]（解析层已做，此处防御）。"""
    return {k: _clamp_score(_f(raw.get(k), 5.0), 0.0, 10.0) for k in STAGE3_DIM_KEYS}


def copy_dimensions_for_audit(raw: Mapping[str, Any]) -> Dict[str, float]:
    return normalize_raw_dimensions(raw)
