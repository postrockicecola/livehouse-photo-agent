"""VLM-driven parametric edit adjustments (Lightroom-style deltas).

Minimal vertical slice: structured numeric output from the editing VLM is parsed
into :class:`EditAdjustments`, then baked by ``op_kernel.apply_parametric_grade``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

# Field ranges. ``exposure`` is in EV stops; the rest follow Lightroom's -100..100 sliders.
_EXPOSURE_RANGE = (-5.0, 5.0)
_SLIDER_RANGE = (-100.0, 100.0)

_SLIDER_FIELDS = (
    "contrast",
    "highlights",
    "shadows",
    "whites",
    "blacks",
    "temp",
    "tint",
    "vibrance",
    "saturation",
    "clarity",
)


def _clamp(value: Any, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v != v:  # NaN guard
        return 0.0
    return float(max(lo, min(hi, v)))


@dataclass(frozen=True)
class EditAdjustments:
    """Per-image parametric edit. Defaults are no-ops (0)."""

    exposure: float = 0.0  # EV stops
    contrast: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    temp: float = 0.0  # + warmer, - cooler
    tint: float = 0.0  # + magenta, - green
    vibrance: float = 0.0
    saturation: float = 0.0
    clarity: float = 0.0

    def is_active(self) -> bool:
        if abs(self.exposure) > 1e-3:
            return True
        return any(abs(getattr(self, k)) > 1e-3 for k in _SLIDER_FIELDS)

    def cache_token(self) -> str:
        parts: list[str] = []
        if abs(self.exposure) > 1e-3:
            parts.append(f"ev{self.exposure:+.2f}")
        for k in _SLIDER_FIELDS:
            v = getattr(self, k)
            if abs(v) > 1e-3:
                parts.append(f"{k[:2]}{v:+.0f}")
        return "|".join(parts)

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def edit_adjustments_from_dict(data: dict[str, Any] | None) -> EditAdjustments:
    """Build clamped adjustments from a raw dict (VLM JSON ``adjustments`` object)."""
    d = data or {}
    return EditAdjustments(
        exposure=_clamp(d.get("exposure", 0.0), *_EXPOSURE_RANGE),
        contrast=_clamp(d.get("contrast", 0.0), *_SLIDER_RANGE),
        highlights=_clamp(d.get("highlights", 0.0), *_SLIDER_RANGE),
        shadows=_clamp(d.get("shadows", 0.0), *_SLIDER_RANGE),
        whites=_clamp(d.get("whites", 0.0), *_SLIDER_RANGE),
        blacks=_clamp(d.get("blacks", 0.0), *_SLIDER_RANGE),
        temp=_clamp(d.get("temp", 0.0), *_SLIDER_RANGE),
        tint=_clamp(d.get("tint", 0.0), *_SLIDER_RANGE),
        vibrance=_clamp(d.get("vibrance", 0.0), *_SLIDER_RANGE),
        saturation=_clamp(d.get("saturation", 0.0), *_SLIDER_RANGE),
        clarity=_clamp(d.get("clarity", 0.0), *_SLIDER_RANGE),
    )


def parse_edit_adjustments_response(json_str: str) -> EditAdjustments:
    """Parse VLM JSON ``{"adjustments": {...}}`` into clamped :class:`EditAdjustments`."""
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return EditAdjustments()
    if not isinstance(data, dict):
        return EditAdjustments()
    adj = data.get("adjustments", data)
    if not isinstance(adj, dict):
        return EditAdjustments()
    return edit_adjustments_from_dict(adj)
