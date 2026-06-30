from utils.gallery_curation import (
    curation_keys_by_verdict,
    curation_liked_keys,
    normalize_gallery_curation,
    read_gallery_curation,
    write_gallery_curation,
)


def test_write_and_read_curation_v2(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    path = write_gallery_curation(
        previews,
        feedback_by_key={
            "a.jpg": {"verdict": "liked", "like_reasons": ["moment", "atmosphere"]},
            "b.jpg": {"verdict": "rejected", "reject_reasons": ["blur_bad"]},
            "c.jpg": {"verdict": "pass"},
        },
        export_by_file={"a.jpg": {"file": "a.jpg", "rotate": 90, "film_variant": "film_cinestill_800t"}},
    )
    assert path is not None
    data = read_gallery_curation(previews)
    assert data is not None
    assert data["version"] == 2
    assert set(data["selected_keys"]) == {"a.jpg"}
    assert curation_liked_keys(data) == {"a.jpg"}
    assert curation_keys_by_verdict(data, "rejected") == {"b.jpg"}
    assert "a.jpg" in data["export_by_file"]


def test_legacy_selected_keys_migrates_to_liked(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    runtime = previews / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "gallery_curation.json").write_text(
        '{"version": 1, "selected_keys": ["x.jpg", "y.jpg"], "export_by_file": {}}',
        encoding="utf-8",
    )
    data = read_gallery_curation(previews)
    assert data is not None
    assert set(data["selected_keys"]) == {"x.jpg", "y.jpg"}
    assert data["feedback_by_key"]["x.jpg"]["verdict"] == "liked"


def test_selected_keys_merge_without_clobbering_rejected(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    write_gallery_curation(
        previews,
        feedback_by_key={"z.jpg": {"verdict": "rejected", "reject_reasons": ["duplicate"]}},
        selected_keys=[],
    )
    write_gallery_curation(
        previews,
        feedback_by_key={"z.jpg": {"verdict": "rejected", "reject_reasons": ["duplicate"]}},
        selected_keys=["a.jpg"],
    )
    data = read_gallery_curation(previews)
    assert curation_liked_keys(data) == {"a.jpg"}
    assert curation_keys_by_verdict(data, "rejected") == {"z.jpg"}
