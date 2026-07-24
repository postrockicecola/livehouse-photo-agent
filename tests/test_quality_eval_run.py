"""Tests for eval_run.v1 emit + baseline diff."""
from __future__ import annotations

from pathlib import Path

import pytest

from quality.eval_run import (
    build_eval_run,
    diff_eval_runs,
    emit_from_stage3_report,
    item_scores_from_joined,
    metrics_from_stage3_report,
    write_eval_run_bundle,
)
from quality.manifest import build_version_manifest
from quality.validate_contracts import validate_document
from scripts.eval.labels import join_labels_predictions, load_labels, load_predictions
from scripts.eval_stage3 import build_report

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "quality" / "fixtures" / "smoke"
_EVAL_CFG = _REPO / "configs" / "eval_stage3.yaml"


@pytest.mark.skipif(not _EVAL_CFG.is_file(), reason="eval_stage3.yaml missing")
def test_emit_eval_run_and_diff(tmp_path):
    labels = load_labels(_FIXTURE / "labels.jsonl")
    preds = load_predictions(_FIXTURE / "predictions.json")
    joined = join_labels_predictions(labels, preds)
    report = build_report(joined, [3, 5])
    vm = build_version_manifest(
        config_path=_EVAL_CFG,
        labels_path=_FIXTURE / "labels.jsonl",
        dataset_manifest_path=_FIXTURE / "manifest.json",
        dataset_name="smoke_fixture",
        dataset_version="0.1.0",
        manifest_id="unit_eval_run",
        created_at="2026-07-24T00:00:00+00:00",
    )
    scores = item_scores_from_joined(joined.pairs)
    run1, _ = emit_from_stage3_report(
        report,
        artifact_root=tmp_path / "runs",
        version_manifest=vm,
        item_scores=scores,
        tags=["unit"],
    )
    assert (Path(run1["artifact_root"]) / "run.json").is_file()
    assert (Path(run1["artifact_root"]) / "metrics.json").is_file()
    assert (Path(run1["artifact_root"]) / "version_manifest.json").is_file()
    assert validate_document(run1, "run1") == []

    # Perturb predictions to create regressors.
    preds2 = load_predictions(_FIXTURE / "predictions.json")
    for p in preds2:
        if p.overall is not None:
            p.overall = float(p.overall) + 15.0
    joined2 = join_labels_predictions(labels, preds2)
    report2 = build_report(joined2, [3, 5])
    run2, diff = emit_from_stage3_report(
        report2,
        artifact_root=tmp_path / "runs",
        version_manifest=vm,
        baseline_path=run1["artifact_root"],
        item_scores=item_scores_from_joined(joined2.pairs),
        tags=["unit", "perturbed"],
    )
    assert diff is not None
    assert run2["baseline_run_id"] == run1["eval_run_id"]
    assert (Path(run2["artifact_root"]) / "diff.json").is_file()
    assert diff.get("metric_deltas")
    # MAE should worsen after +15 bias.
    mae_rows = [r for r in diff["metric_deltas"] if r["metric"] == "mae_overall"]
    assert mae_rows and mae_rows[0]["delta"] is not None
    assert mae_rows[0]["delta"] > 0
    assert diff.get("top_regressors")


def test_metrics_schema_and_build_eval_run():
    metrics = metrics_from_stage3_report(
        {
            "matched": 8,
            "overall": {"n": 8, "spearman": 0.9, "pearson": 0.91, "mae": 2.0, "rmse": 3.0},
            "per_dimension": {},
            "macro_dim_mae": 1.0,
            "selection": {"n": 8, "n_positives": 4, "at_k": []},
        }
    )
    assert metrics["schema"] == "metrics.stage3_scoring.v1"
    run = build_eval_run(
        suite="stage3_scoring",
        metrics=metrics,
        version_manifest={
            "manifest_id": "m",
            "version_manifest_hash": "a" * 64,
        },
        dataset={"name": "smoke_fixture", "version": "0.1.0"},
        artifact_root="quality/store/runs/evr_test",
        eval_run_id="evr_test00000000000000000001",
    )
    assert validate_document(run, "run") == []
    # write_eval_run_bundle validates again
    # (called indirectly in emit test)


def test_diff_self_zero(tmp_path):
    run = {
        "eval_run_id": "evr_a",
        "metrics": {
            "schema": "metrics.stage3_scoring.v1",
            "spearman_overall": 0.5,
            "mae_overall": 4.0,
        },
    }
    path = write_eval_run_bundle(
        tmp_path / "evr_a",
        eval_run=build_eval_run(
            suite="stage3_scoring",
            metrics=run["metrics"],
            version_manifest={"manifest_id": "m", "version_manifest_hash": "b" * 64},
            dataset={"name": "x", "version": "0.1.0"},
            artifact_root=tmp_path / "evr_a",
            eval_run_id="evr_a",
        ),
    )
    loaded = __import__("json").loads(path.read_text(encoding="utf-8"))
    d = diff_eval_runs(loaded, loaded)
    for row in d["metric_deltas"]:
        if row["delta"] is not None:
            assert abs(row["delta"]) < 1e-12
