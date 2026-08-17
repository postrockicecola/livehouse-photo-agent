"""Existing keep-source agreement and parse-fail exclusion from correlation."""
from __future__ import annotations

from pathlib import Path

from scripts.eval.eval_existing_keep_agreement import _pair, build_report
from scripts.eval.labels import Label, Prediction, join_labels_predictions
from scripts.eval_stage3 import build_report as stage3_report


def test_keep_pair_agreement() -> None:
    row = _pair(
        "a",
        {"x": True, "y": False, "z": True},
        "b",
        {"x": True, "y": True, "z": True},
    )
    assert row["n"] == 3
    assert abs(row["agreement"] - 2 / 3) < 1e-9


def test_keep_agreement_skips_missing_files(tmp_path: Path) -> None:
    labels = tmp_path / "a.jsonl"
    labels.write_text('{"file": "a.jpg", "keep": true}\n', encoding="utf-8")
    report = build_report({"only": labels, "gone": tmp_path / "missing.jsonl"})
    assert report["independent_second_rater"] is False
    assert report["sources"]["only"]["n_keep_labels"] == 1
    assert report["pairs"] == []
    assert report["missing"]


def test_parse_fail_excluded_from_correlation() -> None:
    labels = [
        Label(file="ok.jpg", overall=80.0, keep=True),
        Label(file="bad.jpg", overall=20.0, keep=False),
    ]
    preds = [
        Prediction(file="ok.jpg", overall=82.0, parse_meta={"status": "ok"}),
        Prediction(
            file="bad.jpg",
            overall=55.0,
            parse_meta={"status": "fail", "missing_dims": ["focus_sharpness"]},
        ),
    ]
    report = stage3_report(join_labels_predictions(labels, preds), [1])
    assert report["n_excluded_parse_fail"] == 1
    assert report["overall"]["n"] == 1
    assert report["validity"]["parse_fail"] == 1
