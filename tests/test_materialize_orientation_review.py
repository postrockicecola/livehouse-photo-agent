import json

from PIL import Image

from scripts.eval.materialize_orientation_review import materialize


def test_materialize_applies_clockwise_rotation(tmp_path) -> None:
    source = tmp_path / "source.jpg"
    Image.new("RGB", (12, 8)).save(source)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"items": [{"file": "frame.jpg", "source_path": str(source)}]}),
        encoding="utf-8",
    )
    review = tmp_path / "orientation.json"
    review.write_text(
        json.dumps(
            {
                "items": {
                    "frame.jpg": {"rotation_degrees": 90, "reviewed": True}
                }
            }
        ),
        encoding="utf-8",
    )

    result = materialize(manifest, review, tmp_path / "output")

    assert result["count"] == 1
    with Image.open(tmp_path / "output/frame.jpg") as image:
        assert image.size == (8, 12)
