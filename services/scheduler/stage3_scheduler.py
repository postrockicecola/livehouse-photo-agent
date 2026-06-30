"""
Wall-clock budget gate for Stage3 (VLM) inference.

Used to cap how many sequential or admitted images run within a fixed time window.
``should_continue()`` follows the conservative rule: refuse to start another inference once
remaining wall time is below ``estimated_inference_seconds`` (typically one image's p50–p95).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Literal

EarlyStopReason = Literal[
    "none",
    "budget_insufficient_for_next_inference",
    "budget_exhausted",
]

logger = logging.getLogger(__name__)


@dataclass
class Stage3Scheduler:
    """
    Track elapsed wall time against a global Stage3 budget and gate new VLM tasks.

    Use ``remaining_time()`` / ``should_continue()`` **before** starting each inference.
    Parallel pools can overshoot unless ``max_workers==1`` or admission is guarded by a lock
    around the check-plus-submit path.
    """

    time_budget_seconds: float
    estimated_inference_seconds: float = 45.0
    _started_at: float = field(default_factory=lambda: time.monotonic(), repr=False)

    processed_count: int = 0
    early_stop_reason: EarlyStopReason = "none"
    deferred_count: int = 0

    def __post_init__(self) -> None:
        tb = float(self.time_budget_seconds)
        if tb < 0:
            raise ValueError("time_budget_seconds must be >= 0")
        self.time_budget_seconds = tb
        est = float(self.estimated_inference_seconds)
        if est <= 0:
            raise ValueError("estimated_inference_seconds must be > 0")
        self.estimated_inference_seconds = est

    def remaining_time(self) -> float:
        """Seconds left before the Stage3 deadline (never negative)."""
        elapsed = time.monotonic() - self._started_at
        return max(0.0, self.time_budget_seconds - elapsed)

    def should_continue(self) -> bool:
        """
        Whether it is safe to **start** another inference: enough budget remains for one
        full estimated call (upper bound guard for ~30–50 s runs).
        """
        if self.time_budget_seconds <= 0:
            return False
        return self.remaining_time() >= self.estimated_inference_seconds

    def elapsed_seconds(self) -> float:
        """Monotonic wall time since the scheduler started (for totals / logging)."""
        return time.monotonic() - self._started_at

    def mark_processed(self, n: int = 1) -> None:
        self.processed_count += max(0, int(n))

    def mark_skipped_candidates(self, n: int = 1) -> None:
        self.deferred_count += max(0, int(n))

    def set_early_stop(self, reason: EarlyStopReason) -> None:
        self.early_stop_reason = reason

    def log_summary(
        self,
        *,
        lg: logging.Logger | None = None,
        processed_override: int | None = None,
    ) -> None:
        """Emit standard budget summary: totals, elapsed, early-stop reason."""
        log = lg or logger
        n = processed_override if processed_override is not None else self.processed_count
        used = self.elapsed_seconds()
        log.info(
            "stage3_budget summary processed=%s time_used_sec=%.2f remaining_sec=%.2f "
            "budget_sec=%.2f estimate_per_image_sec=%.2f early_stop=%s deferred_candidates=%s",
            n,
            used,
            self.remaining_time(),
            self.time_budget_seconds,
            self.estimated_inference_seconds,
            self.early_stop_reason,
            self.deferred_count,
        )


__all__ = ["EarlyStopReason", "Stage3Scheduler"]
