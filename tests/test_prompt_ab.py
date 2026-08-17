"""Sealed holdout, canary A/B decision, and defect-head isolation."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval.labels import Label, Prediction
from scripts.eval.prompt_ab import (
    CANARY_N,
    HOLDOUT_N,
    HoldoutSealedError,
    build_splits,
    decide,
    require_split,
    score_predictions_on_split,
)
from services.processor.stages.stage3_prompt_builder import (
    build_stage3_defect_head_prompt,
    build_stage3_prompt,
)

_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "selection_v1"


def _items() -> list[dict]:
    rows = []
    for i, kind in enumerate(
        ["technical_hard"] * 50
        + ["semantic_defect"] * 50
        + ["ordinary"] * 100
        + ["highlight"] * 50
    ):
        rows.append({"file": f"{kind}_{i:03d}.jpg", "sample_type": kind})
    return rows


def test_splits_are_disjoint_and_sized() -> None:
    splits = build_splits(_items(), seed=20260817)
    holdout = set(splits["holdout"])
    canary = set(splits["canary"])
    open_files = set(splits["open"])
    assert len(holdout) == HOLDOUT_N
    assert len(canary) == CANARY_N
    assert holdout.isdisjoint(canary)
    assert canary <= open_files
    assert holdout.isdisjoint(open_files)


def test_holdout_requires_explicit_accept(tmp_path: Path) -> None:
    with pytest.raises(HoldoutSealedError):
        require_split("holdout", accept_holdout=False)
    require_split("holdout", accept_holdout=True)
    require_split("canary", accept_holdout=False)

    from scripts.eval.run_prompt_ab import main as run_prompt_ab

    dummy = tmp_path / "preds.jsonl"
    dummy.write_text('{"file":"x.jpg","score":1}\n', encoding="utf-8")
    assert (
        run_prompt_ab(
            [
                "--baseline",
                str(dummy),
                "--candidate",
                str(dummy),
                "--labels",
                str(dummy),
                "--splits",
                str(dummy),
                "--split",
                "holdout",
            ]
        )
        == 2
    )


def test_decision_ignores_spearman_and_uses_keep_and_blunder() -> None:
    baseline = {
        "validity": {"n_known": 0},
        "human_keep": {"precision_at_5": 0.8, "precision_at_10": 0.7},
        "zero_blunder": {"5": {"defect_count": 1}, "10": {"defect_count": 1}},
        "diagnostics": {"spearman": 0.9},
    }
    worse_corr = {
        "validity": {"n_known": 0},
        "human_keep": {"precision_at_5": 1.0, "precision_at_10": 0.8},
        "zero_blunder": {"5": {"defect_count": 0}, "10": {"defect_count": 0}},
        "diagnostics": {"spearman": 0.1},
    }
    assert decide(baseline, worse_corr)["winner"] == "candidate"

    worse_keep = {
        "validity": {"n_known": 0},
        "human_keep": {"precision_at_5": 0.4, "precision_at_10": 0.4},
        "zero_blunder": {"5": {"defect_count": 0}, "10": {"defect_count": 0}},
        "diagnostics": {"spearman": 0.99},
    }
    assert decide(baseline, worse_keep)["winner"] == "baseline"


def test_zero_blunder_counts_defects_in_topk() -> None:
    labels = [
        Label(file="a.jpg", keep=True),
        Label(file="b.jpg", keep=False),
        Label(file="c.jpg", keep=True),
    ]
    preds = [
        Prediction(file="a.jpg", overall=90),
        Prediction(file="b.jpg", overall=80),
        Prediction(file="c.jpg", overall=10),
    ]
    scored = score_predictions_on_split(
        labels=labels,
        predictions=preds,
        files=["a.jpg", "b.jpg", "c.jpg"],
        topks=[2],
    )
    assert scored["zero_blunder"]["2"]["defect_count"] == 1
    assert scored["human_keep"]["n"] == 3


def test_semantic_gate_is_diagnostic_when_not_enforced() -> None:
    from scripts.eval.run_selection_quality_eval import _threshold_failures, load_config

    config, _ = load_config(
        Path(__file__).resolve().parents[1] / "configs" / "eval" / "selection_v1.yaml"
    )
    assert config["thresholds"]["enforce_semantic_gate"] is False
    report = {
        "pipeline_metrics": {
            "stage1": {
                "recall": {"value": 1.0},
                "class_rejection": {
                    "ordinary": {"rate": {"value": 0.0}},
                    "highlight": {"rate": {"value": 0.0}},
                },
            },
            "semantic_gate": {
                "recall": {"value": 0.02},
                "class_rejection": {
                    "ordinary": {"rate": {"value": 0.0}},
                    "highlight": {"rate": {"value": 0.0}},
                },
            },
        },
        "selection_metrics": {
            "global": {"overlap_at_k": 1.0, "defect_count": 0},
            "macro_pack_overlap_at_k": 1.0,
            "pack_zero_blunder_passed": True,
        },
        "validity": {"n_known": 0},
        "runtime_metrics": {"fallback_count": 0, "schema_success_rate": 1.0},
    }
    failures = _threshold_failures(report, config, mode="gated_full_vlm")
    assert not any(str(row.get("metric") or "").startswith("semantic_gate.") for row in failures)

    config["thresholds"]["enforce_semantic_gate"] = True
    enforced = _threshold_failures(report, config, mode="gated_full_vlm")
    assert any(row.get("metric") == "semantic_gate.recall" for row in enforced)


def test_defect_head_has_no_eight_dim_rubric() -> None:
    head = build_stage3_defect_head_prompt()
    full = build_stage3_prompt(blur_eff=None, stage1_features=None)
    assert "focus_sharpness" not in head
    assert "is_present" in head
    assert "focus_sharpness" in full
    assert len(head) < len(full) / 3


@pytest.mark.skipif(
    not (_DATASET / "frozen_manifest.json").is_file(),
    reason="selection_v1 freeze not present",
)
def test_written_splits_match_policy(tmp_path: Path) -> None:
    import shutil

    from scripts.eval.prompt_ab import write_splits

    dest = tmp_path / "selection_v1"
    dest.mkdir()
    shutil.copy(_DATASET / "frozen_manifest.json", dest / "frozen_manifest.json")
    splits = write_splits(dest)
    assert splits["counts"]["holdout"] == HOLDOUT_N
    assert splits["counts"]["canary"] == CANARY_N
    assert set(splits["canary"]).isdisjoint(splits["holdout"])
    assert (dest / "prompt_ab_splits.json").is_file()
