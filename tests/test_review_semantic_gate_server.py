import io
import json

import pytest
from PIL import Image

from scripts.eval.review_semantic_gate_server import ReviewState, normalize_review


def test_normalize_semantic_review_requires_type_and_material_severity() -> None:
    with pytest.raises(ValueError, match="defect type"):
        normalize_review(
            {
                "file": "frame.jpg",
                "disposition": "semantic_reject",
                "types": [],
                "severity": 3,
            },
            {"frame.jpg"},
        )
    with pytest.raises(ValueError, match="severity"):
        normalize_review(
            {
                "file": "frame.jpg",
                "disposition": "semantic_reject",
                "types": ["heavy_occlusion"],
                "severity": 1,
            },
            {"frame.jpg"},
        )


def test_normalize_pass_clears_semantic_fields() -> None:
    result = normalize_review(
        {
            "file": "../frame.jpg",
            "disposition": "pass",
            "types": ["heavy_occlusion"],
            "severity": 3,
            "evidence": "No issue after review.",
        },
        {"frame.jpg"},
    )
    assert result["file"] == "frame.jpg"
    assert result["semantic_gate"]["is_present"] is False
    assert result["semantic_gate"]["types"] == []
    assert result["semantic_gate"]["severity"] == 0


def test_review_state_applies_orientation_metadata(tmp_path) -> None:
    images = tmp_path / "images"
    images.mkdir()
    Image.new("RGB", (12, 8)).save(images / "frame.jpg")
    suggestions = tmp_path / "suggestions.jsonl"
    suggestions.write_text('{"file":"frame.jpg"}\n', encoding="utf-8")
    orientation = tmp_path / "orientation.json"
    orientation.write_text(
        json.dumps(
            {"items": {"frame.jpg": {"rotation_degrees": 90, "reviewed": True}}}
        ),
        encoding="utf-8",
    )
    state = ReviewState(
        suggestions,
        images,
        tmp_path / "reviews.jsonl",
        orientation,
    )

    with Image.open(io.BytesIO(state.image_bytes("frame.jpg"))) as rendered:
        assert rendered.size == (8, 12)
