"""
Dynamic Stage3 degradation under inference queue pressure.

Uses :mod:`infra.metrics` for:

- priority-queue depth (``snapshot_inference_queue_metrics`` / runtime snapshot)
- rolling average inference latency (process-local provider windows)

Integration example (YAML + usage) is documented at the end of this module.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)


class DegradeLevel(str, Enum):
    """Escalating degradation; ``NONE`` means full Stage3."""

    NONE = "none"
    REDUCE_TOP_K = "reduce_top_k"
    SKIP_STAGE3 = "skip_stage3"


@dataclass(frozen=True)
class DegradeThresholds:
    """Trigger boundaries (any exceeded contributes toward degradation)."""

    queue_soft: int = 8
    """Above this (with soft latency): reduce resolution + trim candidates."""

    queue_hard: int = 24
    """At or above this: skip Stage3 entirely."""

    avg_latency_soft_ms: int | None = 15_000
    avg_latency_hard_ms: int | None = 60_000

    top_k_ratio_when_reduced: float = 0.45
    """Keep ``ceil(n * ratio)`` tasks after priority ordering when soft-overloaded."""

    base_thumbnail_max_side: int = 768
    thumbnail_scale_when_reduced: float = 0.72

    provider_filter: str | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> DegradeThresholds:
        if not raw:
            return cls()

        def _i(key: str, default: int | None) -> int | None:
            if key not in raw or raw[key] is None:
                return default
            try:
                v = int(raw[key])
                return v if v > 0 else None
            except (TypeError, ValueError):
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        pf = raw.get("provider_filter")
        ps = str(pf).strip() if pf is not None else None

        return cls(
            queue_soft=max(1, _i("queue_soft", 8) or 8),
            queue_hard=max(1, _i("queue_hard", 24) or 24),
            avg_latency_soft_ms=_i("avg_latency_soft_ms", 15_000),
            avg_latency_hard_ms=_i("avg_latency_hard_ms", 60_000),
            top_k_ratio_when_reduced=max(0.05, min(1.0, _f("top_k_ratio_when_reduced", 0.45))),
            base_thumbnail_max_side=max(320, min(2048, _i("base_thumbnail_max_side", 768) or 768)),
            thumbnail_scale_when_reduced=max(0.35, min(1.0, _f("thumbnail_scale_when_reduced", 0.72))),
            provider_filter=ps if ps else None,
        )

    @classmethod
    def from_env(cls) -> DegradeThresholds:
        def ei(key: str, default: int | None) -> int | None:
            raw = os.environ.get(key)
            if raw is None or raw.strip() == "":
                return default
            try:
                v = int(raw)
                return v if v > 0 else None
            except ValueError:
                return default

        return cls(
            queue_soft=max(1, ei("LIVEHOUSE_STAGE3_DEGRADE_QUEUE_SOFT", 8) or 8),
            queue_hard=max(1, ei("LIVEHOUSE_STAGE3_DEGRADE_QUEUE_HARD", 24) or 24),
            avg_latency_soft_ms=ei("LIVEHOUSE_STAGE3_DEGRADE_LATENCY_SOFT_MS", 15_000),
            avg_latency_hard_ms=ei("LIVEHOUSE_STAGE3_DEGRADE_LATENCY_HARD_MS", 60_000),
            top_k_ratio_when_reduced=max(
                0.05,
                min(1.0, float(os.environ.get("LIVEHOUSE_STAGE3_DEGRADE_TOP_K_RATIO", "0.45"))),
            ),
            base_thumbnail_max_side=max(
                320,
                min(2048, ei("LIVEHOUSE_STAGE3_DEGRADE_BASE_THUMB_SIDE", 768) or 768),
            ),
            thumbnail_scale_when_reduced=max(
                0.35,
                min(1.0, float(os.environ.get("LIVEHOUSE_STAGE3_DEGRADE_THUMB_SCALE", "0.72"))),
            ),
            provider_filter=os.environ.get("LIVEHOUSE_STAGE3_DEGRADE_PROVIDER"),
        )


@dataclass(frozen=True)
class DegradeDecision:
    """Outcome of :meth:`Stage3DegradeController.evaluate`."""

    level: DegradeLevel
    run_stage3: bool
    reasons: tuple[str, ...]
    queue_depth: int
    avg_latency_ms: int | None
    top_k_fraction: float
    thumbnail_max_side: int
    inference_extra_metadata: dict[str, Any] = field(default_factory=dict)


def should_run_stage3(decision: DegradeDecision) -> bool:
    return bool(decision.run_stage3)


def _merge_inference_md(base: Mapping[str, Any], thumbnail_side: int, level: DegradeLevel) -> dict[str, Any]:
    out = dict(base)
    out["vlm_thumbnail_max_side"] = int(thumbnail_side)
    out["stage3_degrade_level"] = level.value
    return out


def _snapshot_signals(
    *,
    provider_filter: str | None,
    metrics_reader: Callable[[], tuple[int, int | None]] | None,
) -> tuple[int, int | None]:
    if metrics_reader is not None:
        return metrics_reader()
    try:
        from infra import metrics as infra_metrics

        depth = int(infra_metrics.inference_queue_runtime_snapshot().get("depth") or 0)
        prov = infra_metrics.provider_runtime_metrics()
        avg_global = prov.get("avg_latency_ms")
        lat: int | None = int(avg_global) if avg_global is not None else None
        if provider_filter:
            for row in prov.get("providers") or []:
                if str(row.get("provider") or "") != str(provider_filter):
                    continue
                v = row.get("avg_latency_ms")
                lat = int(v) if v is not None else lat
                break
        return depth, lat
    except Exception:
        logger.debug("degrade snapshot: infra.metrics unavailable", exc_info=True)
        return 0, None


class Stage3DegradeController:
    """
    Decide whether to run Stage3 and how aggressively to trim candidates / thumbnails.

    Typical wiring: create once per pipeline job from ``processing.stage3_degrade`` config,
    call :meth:`evaluate` before draining Stage3 work.
    """

    def __init__(
        self,
        thresholds: DegradeThresholds | None = None,
        *,
        enabled: bool = True,
        metrics_reader: Callable[[], tuple[int, int | None]] | None = None,
        log_every_evaluation: bool = False,
    ) -> None:
        self.thresholds = thresholds or DegradeThresholds()
        self.enabled = enabled
        self._metrics_reader = metrics_reader
        self._log_every_evaluation = log_every_evaluation
        self._last_level: DegradeLevel | None = None

    @classmethod
    def from_processing_config(cls, processing_cfg: Mapping[str, Any] | None) -> Stage3DegradeController:
        proc = processing_cfg or {}
        if "stage3_degrade" not in proc:
            return cls(enabled=False)
        raw = proc.get("stage3_degrade") or {}
        if not isinstance(raw, dict):
            return cls(enabled=False)
        en = raw.get("enabled", True)
        if isinstance(en, str):
            enabled = en.strip().lower() in ("1", "true", "yes", "on")
        else:
            enabled = bool(en)
        th = DegradeThresholds.from_mapping(raw.get("thresholds") or raw)
        log_every = bool(raw.get("log_every_evaluation", False))
        return cls(thresholds=th, enabled=enabled, log_every_evaluation=log_every)

    def evaluate(self) -> DegradeDecision:
        """Compute overload signals and return a :class:`DegradeDecision`."""
        th = self.thresholds
        if not self.enabled:
            return DegradeDecision(
                level=DegradeLevel.NONE,
                run_stage3=True,
                reasons=("degrade_disabled",),
                queue_depth=0,
                avg_latency_ms=None,
                top_k_fraction=1.0,
                thumbnail_max_side=th.base_thumbnail_max_side,
                inference_extra_metadata=_merge_inference_md({}, th.base_thumbnail_max_side, DegradeLevel.NONE),
            )

        depth, avg_lat = _snapshot_signals(
            provider_filter=th.provider_filter,
            metrics_reader=self._metrics_reader,
        )
        reasons: list[str] = []

        q_hard = depth >= th.queue_hard
        q_soft = depth >= th.queue_soft
        lat_hard = avg_lat is not None and th.avg_latency_hard_ms is not None and avg_lat >= th.avg_latency_hard_ms
        lat_soft = avg_lat is not None and th.avg_latency_soft_ms is not None and avg_lat >= th.avg_latency_soft_ms

        if q_hard:
            reasons.append(f"queue_depth>={th.queue_hard} (observed={depth})")
        elif q_soft:
            reasons.append(f"queue_depth>={th.queue_soft} (observed={depth})")

        if avg_lat is not None:
            if lat_hard:
                reasons.append(f"avg_latency_ms>={th.avg_latency_hard_ms} (observed={avg_lat})")
            elif lat_soft:
                reasons.append(f"avg_latency_ms>={th.avg_latency_soft_ms} (observed={avg_lat})")

        skip = q_hard or lat_hard
        soft_pressure = (q_soft or lat_soft) and not skip

        level = DegradeLevel.NONE
        top_frac = 1.0
        thumb_side = th.base_thumbnail_max_side

        if skip:
            level = DegradeLevel.SKIP_STAGE3
            reasons.append("policy: skip_stage3_on_hard_threshold")
        elif soft_pressure:
            level = DegradeLevel.REDUCE_TOP_K
            top_frac = th.top_k_ratio_when_reduced
            thumb_side = max(
                320,
                int(round(th.base_thumbnail_max_side * th.thumbnail_scale_when_reduced)),
            )
            reasons.append("policy: reduce_top_k_and_resolution_on_soft_threshold")

        run_stage3 = level != DegradeLevel.SKIP_STAGE3

        decision = DegradeDecision(
            level=level,
            run_stage3=run_stage3,
            reasons=tuple(reasons) if reasons else ("healthy",),
            queue_depth=depth,
            avg_latency_ms=avg_lat,
            top_k_fraction=float(top_frac),
            thumbnail_max_side=int(thumb_side),
            inference_extra_metadata=_merge_inference_md(
                {},
                int(thumb_side),
                level if level != DegradeLevel.NONE else DegradeLevel.NONE,
            ),
        )

        esc = level != DegradeLevel.NONE
        changed = self._last_level != level
        if esc and (changed or self._log_every_evaluation):
            logger.warning(
                "stage3_degrade level=%s run_stage3=%s queue_depth=%s avg_latency_ms=%s reasons=%s "
                "top_k_fraction=%.3f thumbnail_max_side=%s",
                decision.level.value,
                decision.run_stage3,
                decision.queue_depth,
                decision.avg_latency_ms,
                list(decision.reasons),
                decision.top_k_fraction,
                decision.thumbnail_max_side,
            )
        self._last_level = level

        return decision


def apply_top_k_fraction(work_len: int, fraction: float) -> int:
    """Candidate cap after reorder-by-score: at least one image when ``work_len`` > 0."""
    if work_len <= 0:
        return 0
    f = max(0.0, min(1.0, float(fraction)))
    return max(1, int(math.ceil(work_len * f)))


# --- Integration example (reference only; not executed) ---
#
# configs/livehouse.yaml::
#
#   processing:
#     stage3_degrade:
#       enabled: true
#       thresholds:
#         queue_soft: 8
#         queue_hard: 24
#         avg_latency_soft_ms: 15000
#         avg_latency_hard_ms: 60000
#
# Python::
#
#   from services.scheduler.degrade_controller import Stage3DegradeController, should_run_stage3
#
#   ctl = Stage3DegradeController.from_processing_config(cfg.get("processing") or {})
#   decision = ctl.evaluate()
#   if not should_run_stage3(decision):
#       defer_all_stage3_candidates(...)
#   else:
#       work_items = work_items[: apply_top_k_fraction(len(work_items), decision.top_k_fraction)]
#       analyze_with_dimensions(..., inference_extra_metadata=dict(decision.inference_extra_metadata))

__all__ = [
    "DegradeDecision",
    "DegradeLevel",
    "DegradeThresholds",
    "Stage3DegradeController",
    "apply_top_k_fraction",
    "should_run_stage3",
]
