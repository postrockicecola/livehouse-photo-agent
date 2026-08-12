import pytest

from agent_eval.scorers.pipeline_quality import (
    binary_metrics,
    build_fixed_packs,
    evaluate_agent_selection,
    evaluate_pipeline,
    score_ranked_selection,
    wilson_interval,
)


def _labels() -> list[dict]:
    rows = []
    counts = {
        "technical_hard": 50,
        "semantic_defect": 50,
        "ordinary": 100,
        "highlight": 50,
    }
    for category, count in counts.items():
        for index in range(count):
            rows.append(
                {
                    "file": f"{category}_{index:03d}.jpg",
                    "sample_type": category,
                    "defect_reasons": (
                        ["blur"] if category == "technical_hard" else
                        ["occlusion"] if category == "semantic_defect" else []
                    ),
                }
            )
    return rows


def test_wilson_and_binary_metrics_include_raw_counts() -> None:
    interval = wilson_interval(48, 50)
    metrics = binary_metrics([True, True, False, False], [True, False, True, False])

    assert interval["value"] == 0.96
    assert interval["low"] < interval["value"] < interval["high"]
    assert metrics["counts"] == {"tp": 1, "fn": 1, "fp": 1, "tn": 1, "total": 4}
    assert metrics["false_positive_rate"] == 0.5


def test_evaluate_pipeline_reports_stage_and_final_errors() -> None:
    labels = [
        {"file": "tech.jpg", "sample_type": "technical_hard", "defect_reasons": ["blur"]},
        {"file": "semantic.jpg", "sample_type": "semantic_defect", "defect_reasons": ["eyes"]},
        {"file": "ordinary.jpg", "sample_type": "ordinary", "defect_reasons": []},
        {"file": "highlight.jpg", "sample_type": "highlight", "defect_reasons": []},
    ]
    predictions = [
        {"file": "tech.jpg", "stage1_reject": True, "semantic_reject": False},
        {"file": "semantic.jpg", "stage1_reject": False, "semantic_reject": True},
        {"file": "ordinary.jpg", "stage1_reject": False, "semantic_reject": False},
        {"file": "highlight.jpg", "stage1_reject": False, "semantic_reject": True},
    ]

    report = evaluate_pipeline(labels, predictions)

    assert report["stage1"]["recall"]["value"] == 1.0
    assert report["semantic_gate"]["recall"]["value"] == 1.0
    assert report["pipeline"]["counts"]["fp"] == 1
    assert report["errors"][0]["file"] == "highlight.jpg"


def test_fixed_packs_are_deterministic_and_exhaustive() -> None:
    labels = _labels()
    sessions = {row["file"]: f"s{index % 21}" for index, row in enumerate(labels)}

    first = build_fixed_packs(labels, sessions, seed=7)
    second = build_fixed_packs(labels, sessions, seed=7)

    assert first == second
    assert len(first) == 10
    assert all(
        pack["counts"]
        == {"technical_hard": 5, "semantic_defect": 5, "ordinary": 10, "highlight": 5}
        for pack in first
    )
    assert len({file_id for pack in first for file_id in pack["files"]}) == 250


def test_ranked_selection_enforces_zero_blunder_and_overlap() -> None:
    result = score_ranked_selection(
        ["good1.jpg", "bad.jpg", "good2.jpg"],
        defects={"bad.jpg"},
        acceptable={"good1.jpg", "good2.jpg"},
        k=3,
    )

    assert result["defect_count"] == 1
    assert result["zero_blunder_passed"] is False
    assert result["overlap_at_k"] == pytest.approx(2 / 3)


def test_agent_selection_rejects_duplicates_and_hallucinations() -> None:
    result = evaluate_agent_selection(
        ["good.jpg", "good.jpg", "invented.jpg"],
        allowed_files={"good.jpg", "bad.jpg"},
        defects={"bad.jpg"},
        acceptable={"good.jpg"},
        k=2,
    )

    assert result["protocol_passed"] is False
    assert result["duplicate_ids"] == ["good.jpg"]
    assert result["hallucinated_ids"] == ["invented.jpg"]
    assert result["selection"]["overlap_count"] == 1
