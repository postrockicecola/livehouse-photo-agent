"""Stage3 latency metrics helpers (no pipeline / ImageHash imports)."""
from __future__ import annotations

import pytest

from services.processor.stage3_latency_metrics import (
    cache_hit_latency_triplet,
    record_stage3_latency_lists,
    wall_sec_from_stage3_meta,
)


def test_wall_sec_prefers_breakdown_sum() -> None:
    meta = {
        "latency_ms": 0,
        "latency_breakdown": {
            "queue_wait_sec": 1.0,
            "model_infer_sec": 2.0,
            "postprocess_sec": 0.5,
        },
    }
    assert wall_sec_from_stage3_meta(meta) == 3.5


def test_record_stage3_latency_lists_rejects_zero_total() -> None:
    stats: dict = {}
    with pytest.raises(AssertionError):
        record_stage3_latency_lists(stats, 0.0, 0.0)


def test_cache_hit_triplet_fast_then_full() -> None:
    hit = {
        "stage3_meta": {
            "stage3_mode": "fast_then_full",
            "latency_ms": 8000,
            "latency_breakdown": {
                "queue_wait_sec": 1.0,
                "model_infer_sec": 7.0,
                "postprocess_sec": 0.0,
            },
            "fast_stage3_meta": {
                "latency_ms": 3000,
                "latency_breakdown": {
                    "queue_wait_sec": 0.5,
                    "model_infer_sec": 2.0,
                    "postprocess_sec": 0.5,
                },
            },
        }
    }
    fast_s, full_s, total = cache_hit_latency_triplet(hit)
    assert fast_s == 3.0
    assert full_s == 8.0
    assert total == pytest.approx(11.0, rel=0, abs=1e-9)
