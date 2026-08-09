from __future__ import annotations

import json

from agent_eval.scorers.selection_scorer import (
    DefectZeroToleranceEvaluator,
    SelectionScorer,
    extract_selected_photo_ids,
)
from scripts.eval.build_defects_db import build_defects_db


def test_extract_selected_photo_ids_preserves_order_and_deduplicates_case() -> None:
    text = """
    {"files": ["photo_001.jpg", "DSC0123.PNG"]}
    - PHOTO_001.JPG
    - IMG_8888.webp
    """

    assert extract_selected_photo_ids(text, max_k=2) == [
        "photo_001.jpg",
        "DSC0123.PNG",
    ]


def test_zero_tolerance_gate_rejects_defects_and_empty_selection(tmp_path) -> None:
    defects_path = tmp_path / "defects.json"
    defects_path.write_text(
        json.dumps(
            {
                "bad.jpg": {
                    "is_defect": True,
                    "reasons": ["out_of_focus"],
                    "overall_score": 31,
                }
            }
        ),
        encoding="utf-8",
    )
    evaluator = DefectZeroToleranceEvaluator(defects_path)

    assert evaluator.evaluate([]).is_passed is False
    result = evaluator.evaluate(["BAD.JPG", "good.jpg"])
    assert result.defect_count == 1
    assert result.defect_rate == 0.5
    assert result.violations[0].reasons == ["out_of_focus"]


def test_selection_scorer_uses_k_as_overlap_denominator(tmp_path) -> None:
    defects_path = tmp_path / "defects.json"
    defects_path.write_text("{}", encoding="utf-8")
    scorer = SelectionScorer(defects_path, min_overlap_threshold=0.8)
    acceptable = [f"photo_{index}.jpg" for index in range(10)]

    passed = scorer.score(acceptable[:8] + ["other.jpg", "another.jpg"], acceptable)
    short = scorer.score(acceptable[:8], acceptable)

    assert passed.overlap_at_k == 0.8
    assert passed.is_passed is True
    assert short.overlap_at_k == 0.8
    assert short.is_passed is True


def test_build_defects_db_supports_nested_and_flat_label_fields(tmp_path) -> None:
    labels_path = tmp_path / "labels.jsonl"
    labels_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "file": "low.jpg",
                        "overall": 32,
                        "dims": {"focus_sharpness": 2},
                    }
                ),
                json.dumps(
                    {
                        "file": "exposure.png",
                        "overall_score": 70,
                        "exposure_control": 1,
                    }
                ),
                json.dumps({"file": "good.webp", "overall": 90, "dims": {}}),
            ]
        ),
        encoding="utf-8",
    )

    defects = build_defects_db(labels_path)

    assert defects["low.jpg"]["reasons"] == [
        "low_overall_score",
        "out_of_focus",
    ]
    assert defects["exposure.png"]["reasons"] == ["poor_exposure"]
    assert "good.webp" not in defects
