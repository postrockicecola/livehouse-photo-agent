"""Underexposure salvage lane for Stage1 → Stage2 gating.

Livehouse frames are often intentionally dark. Stage1 applies soft shadow/brightness
penalties that can push ``tech_score`` under Stage2's floor even when structure remains.

This module never hard-rejects and never replaces the original pixels for VLM scoring.
It only decides whether an underexposed-but-structured frame deserves a tech-score
rebate (and optional fast-score floor relief) so Stage2 can still see it.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

import cv2
import numpy as np

# Defaults; overridable via quality_thresholds / processing.underexposure_salvage.
_SALVAGE_FALLBACK: Dict[str, float] = {
    "enabled": 1.0,
    # Extreme livehouse underexposure (p50≈2–10) needs looser structure floors + higher EV.
    "min_edge_ratio": 0.0015,
    "min_contrast": 4.0,
    "min_p_range": 12.0,
    "max_highlight_frac": 0.22,
    "target_p50": 100.0,
    "ev_min": 0.35,
    "ev_max": 6.5,
    "preview_min_contrast": 4.0,
    "preview_min_edge_ratio": 0.0012,
    "preview_max_highlight_frac": 0.55,
    # Before Stage1 severe-blur hard reject: try EV when midtones are crushed.
    "dark_blur_escape_p50_max": 12.0,
    # Guardrails so true mush (eval DSC06178) does not ride the EV escape.
    "dark_blur_escape_min_edge_ratio": 0.00055,
    "dark_blur_escape_min_preview_laplacian": 380.0,
    "tech_rebate_shadow_soft": 8.0,
    "tech_rebate_shadow_heavy": 16.0,
    "tech_rebate_brightness_low": 5.0,
    "tech_rebate_cap": 22.0,
    "fast_aesthetic_score_min": 16.0,
}

_SHADOW_PENALTY_TAGS = frozenset({"shadow_soft", "shadow_heavy", "brightness_low_soft"})


def _f(cfg: Mapping[str, Any], key: str) -> float:
    raw = cfg.get(key, _SALVAGE_FALLBACK[key])
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(_SALVAGE_FALLBACK[key])


def merge_salvage_config(
    quality_thresholds: Mapping[str, Any] | None = None,
    processing_cfg: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Merge yaml blocks into one salvage config dict."""
    out: Dict[str, Any] = dict(_SALVAGE_FALLBACK)
    q = dict(quality_thresholds or {})
    # Flat keys under quality_thresholds: underexposure_salvage_*
    prefix = "underexposure_salvage_"
    for k, v in q.items():
        if str(k).startswith(prefix):
            out[str(k)[len(prefix) :]] = v
    nested_q = q.get("underexposure_salvage")
    if isinstance(nested_q, Mapping):
        out.update(dict(nested_q))
    proc = dict(processing_cfg or {})
    nested_p = proc.get("underexposure_salvage")
    if isinstance(nested_p, Mapping):
        out.update(dict(nested_p))
    return out


def _shadow_rebate(penalties: List[str], cfg: Mapping[str, Any]) -> float:
    rebate = 0.0
    tags = set(penalties or [])
    if "shadow_heavy" in tags:
        rebate += _f(cfg, "tech_rebate_shadow_heavy")
    elif "shadow_soft" in tags:
        rebate += _f(cfg, "tech_rebate_shadow_soft")
    if "brightness_low_soft" in tags:
        rebate += _f(cfg, "tech_rebate_brightness_low")
    return float(min(_f(cfg, "tech_rebate_cap"), rebate))


def _candidate_gate(
    *,
    penalties: List[str],
    expo: Mapping[str, float],
    edge_ratio: float,
    contrast: float,
    blur_type: str,
    cfg: Mapping[str, Any],
) -> Tuple[bool, str]:
    if _f(cfg, "enabled") <= 0.0:
        return False, "disabled"
    tags = set(penalties or [])
    if not (tags & _SHADOW_PENALTY_TAGS):
        return False, "no_shadow_penalty"
    if float(expo.get("highlight_frac", 0.0) or 0.0) > _f(cfg, "max_highlight_frac"):
        return False, "highlight_too_high"
    p_range = float(expo.get("p99", 0.0) or 0.0) - float(expo.get("p01", 0.0) or 0.0)
    if p_range < _f(cfg, "min_p_range"):
        return False, "dynamic_range_dead"
    p50 = float(expo.get("p50", 0.0) or 0.0)
    # Extreme crush: original Canny/Laplacian edges are unreliable; EV preview gates later.
    dark_crush = p50 <= _f(cfg, "dark_blur_escape_p50_max")
    if not dark_crush and float(contrast) < _f(cfg, "min_contrast"):
        return False, "contrast_too_low"
    if not dark_crush and float(edge_ratio) < _f(cfg, "min_edge_ratio"):
        return False, "edge_too_sparse"
    if blur_type in ("focus_blur",) and not dark_crush:
        # Focus mush is not an exposure problem; do not salvage.
        return False, "focus_blur"
    return True, "candidate"


def _preview_laplacian_var(gray: np.ndarray, ev: float) -> float:
    g = np.clip(gray.astype(np.float32) * (2.0 ** float(ev)), 0.0, 255.0)
    h, w = g.shape[:2]
    if max(h, w) > 640:
        scale = 640.0 / float(max(h, w))
        g = cv2.resize(
            g,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return float(cv2.Laplacian(g, cv2.CV_32F).var())


def try_dark_ev_structure_escape(
    *,
    gray: np.ndarray,
    expo: Mapping[str, float],
    edge_ratio: float = 0.0,
    cfg: Mapping[str, Any] | None = None,
) -> Optional[Dict[str, Any]]:
    """
    Severe-blur hard-reject bypass for crushed underexposure.

    When midtones are near-black, OpenCV edges vanish even if a subject remains.
    A light in-memory EV lift that recovers structure → escape (not a real blur reject).
    """
    c = dict(_SALVAGE_FALLBACK)
    if cfg:
        c.update(dict(cfg))
    if _f(c, "enabled") <= 0.0:
        return None
    p50 = float(expo.get("p50", 0.0) or 0.0)
    if p50 <= 0.0 or p50 > _f(c, "dark_blur_escape_p50_max"):
        return None
    if float(expo.get("highlight_frac", 0.0) or 0.0) > _f(c, "max_highlight_frac"):
        return None
    p_range = float(expo.get("p99", 0.0) or 0.0) - float(expo.get("p01", 0.0) or 0.0)
    if p_range < _f(c, "min_p_range"):
        return None
    # True mush often has almost zero native edges even before crush; keepers retain a whisper.
    if float(edge_ratio) < _f(c, "dark_blur_escape_min_edge_ratio"):
        return None
    ev = _ev_to_target_p50(p50, c)
    if ev <= 0.0:
        return None
    prev = _preview_metrics(gray, ev)
    if prev["contrast"] < _f(c, "preview_min_contrast"):
        return None
    if prev["edge_ratio"] < _f(c, "preview_min_edge_ratio"):
        return None
    if prev["highlight_frac"] > _f(c, "preview_max_highlight_frac"):
        return None
    lift_lap = _preview_laplacian_var(gray, ev)
    if lift_lap < _f(c, "dark_blur_escape_min_preview_laplacian"):
        return None
    prev = dict(prev)
    prev["laplacian_var"] = lift_lap
    return {
        "reason": "underexposure_ev_structure",
        "salvage_ev": float(ev),
        "salvage_preview": prev,
    }


def _ev_to_target_p50(p50: float, cfg: Mapping[str, Any]) -> float:
    target = _f(cfg, "target_p50")
    p50 = max(1.0, float(p50))
    if p50 >= target:
        return 0.0
    ev = math.log2(target / p50)
    return float(max(_f(cfg, "ev_min"), min(_f(cfg, "ev_max"), ev)))


def _preview_metrics(gray: np.ndarray, ev: float) -> Dict[str, float]:
    g = np.clip(gray.astype(np.float32) * (2.0**float(ev)), 0.0, 255.0)
    p01, p50, p99 = (float(x) for x in np.percentile(g, [1, 50, 99]))
    # Match Stage1 adaptive highlight mass (lightweight): pixels near white after lift.
    t_high = float(np.clip(np.percentile(g, 98.5) + 4.0, 200.0, 255.0))
    highlight_frac = float(np.mean(g >= t_high))
    # Sobel magnitude proxy for structure retention (cheap vs Canny).
    # Downsample for speed on large previews.
    h, w = g.shape[:2]
    if max(h, w) > 640:
        scale = 640.0 / float(max(h, w))
        g_s = cv2.resize(
            g,
            (max(1, int(w * scale)), max(1, int(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    else:
        g_s = g

    gx = cv2.Sobel(g_s, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g_s, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    edge_ratio = float(np.mean(mag > 28.0))
    contrast = float(g.std())
    return {
        "p01": p01,
        "p50": p50,
        "p99": p99,
        "highlight_frac": highlight_frac,
        "edge_ratio": edge_ratio,
        "contrast": contrast,
    }


def evaluate_underexposure_salvage(
    *,
    gray: np.ndarray,
    penalties: List[str],
    expo: Mapping[str, float],
    edge_ratio: float,
    contrast: float,
    blur_type: str,
    tech_score: float,
    cfg: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Returns debug fields to merge into Stage1 ``debug_info``.

    On success sets ``underexposure_salvage=True`` and ``effective_tech_score``.
    On skip sets ``underexposure_salvage=False`` and a short ``salvage_skip_reason``.
    """
    c = dict(_SALVAGE_FALLBACK)
    if cfg:
        c.update(dict(cfg))

    ok, why = _candidate_gate(
        penalties=list(penalties or []),
        expo=expo,
        edge_ratio=float(edge_ratio),
        contrast=float(contrast),
        blur_type=str(blur_type or "none"),
        cfg=c,
    )
    if not ok:
        return {
            "underexposure_salvage": False,
            "salvage_skip_reason": why,
            "effective_tech_score": float(tech_score),
        }

    p50 = float(expo.get("p50", 0.0) or 0.0)
    ev = _ev_to_target_p50(p50, c)
    if ev <= 0.0:
        return {
            "underexposure_salvage": False,
            "salvage_skip_reason": "already_midtone",
            "effective_tech_score": float(tech_score),
        }

    prev = _preview_metrics(gray, ev)
    if prev["contrast"] < _f(c, "preview_min_contrast"):
        return {
            "underexposure_salvage": False,
            "salvage_skip_reason": "preview_contrast_flat",
            "salvage_ev": ev,
            "salvage_preview": prev,
            "effective_tech_score": float(tech_score),
        }
    if prev["edge_ratio"] < _f(c, "preview_min_edge_ratio"):
        return {
            "underexposure_salvage": False,
            "salvage_skip_reason": "preview_no_structure",
            "salvage_ev": ev,
            "salvage_preview": prev,
            "effective_tech_score": float(tech_score),
        }
    if prev["highlight_frac"] > _f(c, "preview_max_highlight_frac"):
        return {
            "underexposure_salvage": False,
            "salvage_skip_reason": "preview_blows_out",
            "salvage_ev": ev,
            "salvage_preview": prev,
            "effective_tech_score": float(tech_score),
        }

    rebate = _shadow_rebate(list(penalties or []), c)
    effective = float(min(100.0, max(float(tech_score), float(tech_score) + rebate)))
    return {
        "underexposure_salvage": True,
        "salvage_ev": float(ev),
        "salvage_tech_rebate": float(rebate),
        "salvage_preview": prev,
        "effective_tech_score": effective,
        "salvage_fast_score_min": _f(c, "fast_aesthetic_score_min"),
    }


def apply_salvage_to_debug(
    debug_info: MutableMapping[str, Any],
    salvage: Mapping[str, Any],
) -> None:
    """Merge salvage result into Stage1 debug_info in-place."""
    debug_info.update(dict(salvage))


def stage2_gate_scores(
    tech_score: float,
    fast_score: float,
    *,
    debug_info: Optional[Mapping[str, Any]] = None,
    tech_score_min: float,
    fast_aesthetic_score_min: float,
) -> Tuple[float, float, float, float]:
    """
    Return (tech_used, fast_used, tech_min, fast_min) for Stage2 thresholding.

    Salvage candidates use ``effective_tech_score`` and may lower the fast floor.
    """
    dbg = dict(debug_info or {})
    tech_min = float(tech_score_min)
    fast_min = float(fast_aesthetic_score_min)
    tech_used = float(tech_score)
    fast_used = float(fast_score)
    if dbg.get("underexposure_salvage"):
        try:
            tech_used = float(dbg.get("effective_tech_score", tech_score))
        except (TypeError, ValueError):
            tech_used = float(tech_score)
        raw_fast_floor = dbg.get("salvage_fast_score_min")
        if raw_fast_floor is not None:
            try:
                fast_min = min(fast_min, float(raw_fast_floor))
            except (TypeError, ValueError):
                pass
    return tech_used, fast_used, tech_min, fast_min
