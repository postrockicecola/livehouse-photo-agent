"""Tests for the quantization comparison combiner (scripts/eval/quant_compare).

These cover the pure join/delta math that turns one ``eval_stage3`` quality report and
one ``load_test`` serving report per arm into a single quality-vs-cost comparison — no
files or GPU needed.
"""
from __future__ import annotations

import pytest

from scripts.eval.quant_compare import (
    compare,
    diff_arm,
    parse_arm_spec,
    summarize_arm,
    _pick_scenario,
)


def test_parse_arm_spec_with_and_without_loadtest():
    assert parse_arm_spec("fp16:q.json:l.json") == ("fp16", "q.json", "l.json")
    assert parse_arm_spec("int4:q.json") == ("int4", "q.json", None)


def test_parse_arm_spec_rejects_bad_specs():
    with pytest.raises(ValueError):
        parse_arm_spec("onlylabel")
    with pytest.raises(ValueError):
        parse_arm_spec(":q.json")


def test_pick_scenario_max_and_explicit():
    doc = {"scenarios": [
        {"concurrency": 1, "throughput_rps": 3.0},
        {"concurrency": 8, "throughput_rps": 19.0},
        {"concurrency": 4, "throughput_rps": 12.0},
    ]}
    assert _pick_scenario(doc, "max")["concurrency"] == 8
    assert _pick_scenario(doc, "4")["concurrency"] == 4
    assert _pick_scenario(doc, "99") is None
    assert _pick_scenario({"scenarios": []}, "max") is None


def test_summarize_arm_maps_nan_to_none():
    quality = {"overall": {"n": 250, "spearman": float("nan"), "mae": 6.5}}
    row = summarize_arm("x", quality, None)
    assert row["spearman"] is None  # NaN must not leak into deltas
    assert row["mae"] == 6.5
    assert row["throughput_rps"] is None  # no load-test doc


def test_summarize_arm_joins_serving_fields():
    quality = {"overall": {"spearman": 0.36, "mae": 6.5}}
    load = {"scenarios": [{"concurrency": 8, "throughput_rps": 19.4, "latency_p99_ms": 520,
                           "decode_tokens_per_sec": 1460.0, "est_cost_per_1k_usd": 0.017,
                           "cost_basis": "gpu_hourly"}]}
    row = summarize_arm("int4", quality, load, concurrency="max")
    assert row["concurrency"] == 8
    assert row["throughput_rps"] == 19.4
    assert row["est_cost_per_1k_usd"] == 0.017


def test_diff_arm_cost_savings_and_speedup():
    base = summarize_arm("fp16", {"overall": {"spearman": 0.368, "mae": 6.50}},
                         {"scenarios": [{"concurrency": 8, "throughput_rps": 12.0,
                                         "latency_p99_ms": 740, "decode_tokens_per_sec": 900.0,
                                         "est_cost_per_1k_usd": 0.0278}]})
    arm = summarize_arm("int4", {"overall": {"spearman": 0.359, "mae": 6.71}},
                        {"scenarios": [{"concurrency": 8, "throughput_rps": 19.4,
                                        "latency_p99_ms": 520, "decode_tokens_per_sec": 1460.0,
                                        "est_cost_per_1k_usd": 0.0172}]})
    d = diff_arm(arm, base)
    assert d["d_spearman"] == pytest.approx(-0.009, abs=1e-6)
    assert d["d_mae"] == pytest.approx(0.21, abs=1e-6)
    assert d["throughput_speedup_x"] == pytest.approx(1.617, abs=1e-3)
    # cheaper than baseline → positive savings
    assert d["cost_savings_pct"] == pytest.approx(38.13, abs=0.1)


def test_diff_arm_missing_serving_is_none_not_crash():
    base = summarize_arm("fp16", {"overall": {"spearman": 0.36, "mae": 6.5}}, None)
    arm = summarize_arm("int4", {"overall": {"spearman": 0.35, "mae": 6.6}}, None)
    d = diff_arm(arm, base)
    assert d["d_spearman"] == pytest.approx(-0.01, abs=1e-6)
    assert d["throughput_speedup_x"] is None
    assert d["cost_savings_pct"] is None


def test_compare_requires_known_baseline():
    arms = [summarize_arm("fp16", {"overall": {"spearman": 0.36}}, None)]
    with pytest.raises(ValueError):
        compare(arms, "nonexistent")


def test_compare_excludes_baseline_from_deltas():
    arms = [
        summarize_arm("fp16", {"overall": {"spearman": 0.368, "mae": 6.5}}, None),
        summarize_arm("int4", {"overall": {"spearman": 0.359, "mae": 6.7}}, None),
    ]
    report = compare(arms, "fp16")
    assert report["baseline"] == "fp16"
    assert [d["arm"] for d in report["deltas"]] == ["int4"]
    assert len(report["arms"]) == 2
