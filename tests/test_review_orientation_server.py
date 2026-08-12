import json
from pathlib import Path

import pytest

from scripts.eval.review_orientation_server import OrientationStore, load_manifest


def test_orientation_store_round_trips_review(tmp_path: Path) -> None:
    output = tmp_path / "orientation_review.json"
    store = OrientationStore(output, {"frame.jpg"})

    saved = store.update("frame.jpg", 90, True)
    reloaded = OrientationStore(output, {"frame.jpg"})

    assert saved["rotation_degrees"] == 90
    assert reloaded.snapshot()["frame.jpg"]["reviewed"] is True
    assert json.loads(output.read_text())["schema_version"] == (
        "selection_orientation_review.v1"
    )


def test_orientation_store_rejects_invalid_update(tmp_path: Path) -> None:
    store = OrientationStore(tmp_path / "review.json", {"frame.jpg"})

    with pytest.raises(ValueError, match="unknown file"):
        store.update("other.jpg", 90, True)
    with pytest.raises(ValueError, match="0, 90, 180, or 270"):
        store.update("frame.jpg", 45, True)


def test_load_manifest_validates_sources(tmp_path: Path) -> None:
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"jpeg")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "file": "frame.jpg",
                        "source_path": str(image),
                        "sample_type": "ordinary",
                        "session": "2026-08-10",
                    }
                ]
            }
        )
    )

    assert load_manifest(manifest)[0]["source_path"] == image
