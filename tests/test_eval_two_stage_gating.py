"""Offline two-stage gating replay builds a comparable report."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval.eval_two_stage_gating import build_report, load_predictions, load_stage2_rows, load_truth
from utils.config_loader import ConfigLoader

_PREDICTIONS = Path("data/eval/images/analysis_results.json")
_STAGE2 = Path("data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl")


def test_two_stage_report_has_both_arms():
    if not _PREDICTIONS.is_file() or not _STAGE2.is_file():
        pytest.skip(
            "local eval fixtures missing "
            "(data/eval/images/ and data/eval/_*/ are gitignored)"
        )
    truth = load_truth("data/eval/labels.jsonl")
    predictions = load_predictions(str(_PREDICTIONS))
    stage2 = load_stage2_rows(str(_STAGE2))
    config = ConfigLoader.load("configs/livehouse.yaml")
    report = build_report(
        truth=truth,
        predictions=predictions,
        stage2_rows=stage2,
        config=config,
        topks=[10, 20],
    )
    assert report["eval_set_size"] >= 100
    assert "full_vlm" in report["arms"]
    assert "two_stage_gated" in report["arms"]
    gated = report["arms"]["two_stage_gated"]
    full = report["arms"]["full_vlm"]
    assert gated["vlm_calls"] <= full["vlm_calls"]
    assert gated["vlm_call_share"] is not None
    assert gated["vlm_call_share"] < 1.0
