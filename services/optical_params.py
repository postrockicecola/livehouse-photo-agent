"""Parse Lab optical-console query payloads."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class OpticalConsoleParams:
    """Optical console sliders (0–100 UI scale unless noted)."""

    air: float = 0.0
    halation: float = 0.0
    night: float = 0.0
    dream: float = 0.0
    flow: float = 0.0
    time: float = 0.0
    wear: float = 0.0
    flow_angle: float = -15.0

    def cache_token(self) -> str:
        parts: list[str] = []
        for key in ("air", "halation", "night", "dream", "flow", "time", "wear"):
            v = getattr(self, key)
            if v > 0.0:
                parts.append(f"{key[0]}{v:.0f}")
        if self.flow > 0.0 and abs(self.flow_angle + 15.0) > 0.01:
            parts.append(f"a{self.flow_angle:.0f}")
        return "|".join(parts)

    def is_active(self) -> bool:
        return any(
            getattr(self, k) > 0.0
            for k in ("air", "halation", "night", "dream", "flow", "time", "wear")
        )


# Back-compat alias
OpticalP1Params = OpticalConsoleParams


def parse_optical_console(raw: str | None) -> OpticalConsoleParams | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError("optical must be valid JSON") from e
    if not isinstance(data, dict):
        raise ValueError("optical must be a JSON object")
    params = OpticalConsoleParams(
        air=_coerce_strength(data.get("air", 0.0)),
        halation=_coerce_strength(data.get("halation", 0.0)),
        night=_coerce_strength(data.get("night", 0.0)),
        dream=_coerce_strength(data.get("dream", 0.0)),
        flow=_coerce_strength(data.get("flow", 0.0)),
        time=_coerce_strength(data.get("time", 0.0)),
        wear=_coerce_strength(data.get("wear", 0.0)),
        flow_angle=float(data.get("flow_angle", -15.0)),
    )
    flow_angle = float(max(-90.0, min(90.0, params.flow_angle)))
    params = OpticalConsoleParams(
        air=params.air,
        halation=params.halation,
        night=params.night,
        dream=params.dream,
        flow=params.flow,
        time=params.time,
        wear=params.wear,
        flow_angle=flow_angle,
    )
    return params if params.is_active() else None


def parse_optical_p1(raw: str | None) -> OpticalConsoleParams | None:
    return parse_optical_console(raw)


def _coerce_strength(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return float(max(0.0, min(100.0, v)))


def _strength_multiplier(ui: float, *, span: float = 1.75) -> float:
    """Map UI 0–100 to ~1.0 at 0 and up to ``1+span`` at 100."""
    t = _coerce_strength(ui) / 100.0
    t = float(np.clip(np.power(t, 0.48), 0.0, 1.0))
    return 1.0 + t * span

