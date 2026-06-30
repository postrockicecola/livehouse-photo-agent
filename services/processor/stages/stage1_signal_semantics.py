"""Map Stage1 numeric signals to short semantic labels for VLM prompts."""
from __future__ import annotations

from typing import Any

# Align with ``engine/operators/image_processor.py`` blur ladder defaults.
_LAP_SEVERE = 30.0
_LAP_SLIGHT = 50.0
_LAP_MEDIUM = 120.0
_LAP_HIGH = 200.0


def _bucket_laplacian(v: float) -> str:
    if v < _LAP_SEVERE:
        return "very soft focus"
    if v < _LAP_SLIGHT:
        return "low sharpness"
    if v < _LAP_MEDIUM:
        return "moderate sharpness"
    if v < _LAP_HIGH:
        return "good sharpness"
    return "high sharpness"


def _bucket_unit_fraction(v: float, *, low: str, mid: str, high: str) -> str:
    if v < 0.08:
        return low
    if v < 0.22:
        return mid
    return high


def _bucket_contrast(v: float) -> str:
    if v < 35:
        return "low contrast"
    if v < 55:
        return "medium contrast"
    return "high contrast"


def stage1_semantic_lines(stage1: dict[str, Any] | None) -> str:
    """Hybrid semantic + compact numeric hints for Stage3 task payload."""
    if not stage1:
        return ""

    parts: list[str] = []

    lap = stage1.get("laplacian_var")
    if lap is not None:
        try:
            lv = float(lap)
            parts.append(f"sharpness={_bucket_laplacian(lv)} (laplacian_var={lv:.0f})")
        except (TypeError, ValueError):
            pass

    edge = stage1.get("edge_ratio")
    if edge is not None:
        try:
            ev = float(edge)
            label = "sparse edges" if ev < 0.04 else "moderate edges" if ev < 0.12 else "rich edges"
            parts.append(f"edge_structure={label} (edge_ratio={ev:.3f})")
        except (TypeError, ValueError):
            pass

    hi = stage1.get("highlight_frac")
    if hi is not None:
        try:
            hv = float(hi)
            parts.append(
                f"highlights={_bucket_unit_fraction(hv, low='controlled highlights', mid='bright highlights', high='highlight clipping risk')} "
                f"(highlight_frac={hv:.2f})"
            )
        except (TypeError, ValueError):
            pass

    sh = stage1.get("shadow_frac")
    if sh is not None:
        try:
            sv = float(sh)
            parts.append(
                f"shadows={_bucket_unit_fraction(sv, low='open shadows', mid='deep shadows', high='crushed shadow risk')} "
                f"(shadow_frac={sv:.2f})"
            )
        except (TypeError, ValueError):
            pass

    contrast = stage1.get("contrast")
    if contrast is not None:
        try:
            cv = float(contrast)
            parts.append(f"contrast={_bucket_contrast(cv)} (contrast={cv:.1f})")
        except (TypeError, ValueError):
            pass

    blur_type = stage1.get("blur_type")
    if blur_type is not None and str(blur_type).strip():
        parts.append(f"blur_type={blur_type}")

    if not parts:
        return ""
    return "Image signals: " + "; ".join(parts) + ".\n"
