"""Stage3 cloud VLM token/cost run summary: fast+full accumulation and optional pricing."""
from threading import Lock

from services.processor.stages.deep_analysis import (
    accumulate_stage3_token_usage,
    build_stage3_token_summary,
)


def _tally():
    stats: dict = {}
    lock = Lock()
    # Fast pass runs on every candidate; full only on top-k — both must be counted.
    for _ in range(3):
        accumulate_stage3_token_usage(stats, lock, {"prompt_eval_count": 1000, "eval_count": 120}, mode="fast")
    accumulate_stage3_token_usage(stats, lock, {"prompt_tokens": 1800, "completion_tokens": 300}, mode="full")
    return stats


def test_accumulate_splits_fast_and_full_and_ignores_tokenless():
    stats = _tally()
    # Metadata without token keys / None must not create phantom calls.
    accumulate_stage3_token_usage(stats, Lock(), {"queue_wait_sec": 1.2}, mode="fast")
    accumulate_stage3_token_usage(stats, Lock(), None, mode="full")
    assert stats["stage3_calls_fast"] == 3
    assert stats["stage3_calls_full"] == 1
    assert stats["stage3_prompt_tokens_fast"] == 3000
    assert stats["stage3_completion_tokens_fast"] == 360
    assert stats["stage3_prompt_tokens_full"] == 1800
    assert stats["stage3_completion_tokens_full"] == 300


def test_summary_totals_and_optional_cost():
    stats = _tally()
    cfg = {
        "stage3": {
            "route_b": {
                "pricing": {"input_per_1m": 1.5, "output_per_1m": 4.5, "currency": "CNY"}
            }
        }
    }
    s = build_stage3_token_summary(cfg, stats)
    assert s["prompt_tokens"] == 4800
    assert s["completion_tokens"] == 660
    assert s["total_tokens"] == 5460
    assert s["fast_calls"] == 3 and s["full_calls"] == 1
    assert s["estimated_cost"] == round(4800 / 1e6 * 1.5 + 660 / 1e6 * 4.5, 4)
    assert s["currency"] == "CNY"


def test_summary_without_pricing_reports_tokens_only():
    s = build_stage3_token_summary({"stage3": {"route_b": {}}}, _tally())
    assert s["total_tokens"] == 5460
    assert "estimated_cost" not in s


def test_summary_none_when_no_tokens():
    assert build_stage3_token_summary({"stage3": {"route_b": {}}}, {}) is None
