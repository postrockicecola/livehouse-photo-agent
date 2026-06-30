"""Stage3 latency bookkeeping (no inference / OpenCV deps)."""
from __future__ import annotations

from typing import Any, Mapping


def wall_sec_from_stage3_meta(stage3_meta: Mapping[str, Any] | None) -> float:
    """Recover wall-clock seconds from persisted Stage3 meta (breakdown preferred)."""
    meta = dict(stage3_meta or {})
    lb = meta.get("latency_breakdown") or {}
    if isinstance(lb, dict) and lb.get("queue_wait_sec") is not None:
        w = (
            float(lb.get("queue_wait_sec") or 0)
            + float(lb.get("model_infer_sec") or 0)
            + float(lb.get("postprocess_sec") or 0)
        )
        if w > 0:
            return w
    try:
        ms = float(meta.get("latency_ms") or 0)
    except (TypeError, ValueError):
        ms = 0.0
    return ms / 1000.0


def record_stage3_latency_lists(stats: dict[str, Any], fast_s: float, full_s: float) -> None:
    """Append per-image fast / full / wall latency to ``stats`` (no lock). Raises if wall is not positive."""
    wall = float(fast_s) + float(full_s)
    if wall <= 0.0:
        raise AssertionError(
            f"stage3 total_wall_latency must be > 0 (fast_s={fast_s}, full_s={full_s})"
        )
    stats.setdefault("stage3_fast_pass_latencies_sec", []).append(float(fast_s))
    stats.setdefault("stage3_full_pass_latencies_sec", []).append(float(full_s))
    stats.setdefault("stage3_wall_latencies_sec", []).append(wall)
    stats.setdefault("stage3_latencies_sec", []).append(wall)


def cache_hit_latency_triplet(hit: Mapping[str, Any]) -> tuple[float, float, float]:
    """
    Recover (fast_wall_sec, full_wall_sec, total_wall_sec) from a cached Stage3 dict.

    For ``fast_then_full`` results, fast and full wall times are read from
    ``fast_stage3_meta`` and the top-level ``stage3_meta`` respectively (the latter
    reflects the dimensional pass).
    """
    sm = hit.get("stage3_meta") or {}
    if not isinstance(sm, Mapping):
        sm = {}
    mode = str(sm.get("stage3_mode") or "")
    fsm = sm.get("fast_stage3_meta")
    if mode == "fast_then_full" and isinstance(fsm, Mapping):
        fast_s = wall_sec_from_stage3_meta(fsm)
        full_s = wall_sec_from_stage3_meta(sm)
        total = fast_s + full_s
        return fast_s, full_s, total
    total = wall_sec_from_stage3_meta(sm)
    if mode == "fast_only":
        return total, 0.0, total
    return total, 0.0, total
