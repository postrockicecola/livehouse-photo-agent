from utils.export_feedback import (
    append_export_feedback_event,
    exported_files_aggregate,
    read_export_feedback,
)


def test_append_export_feedback(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    ev = append_export_feedback_event(
        previews,
        category="best",
        use_session_vibe=True,
        session_vibe_film_variant="film_portra_400",
        export_path="/tmp/export_1",
        items=[
            {
                "file": "DSC0001.ARW",
                "rotate": 0,
                "film_variant": "film_cinestill_800t",
                "film_variant_effective": "film_cinestill_800t",
                "jpeg_exported": True,
                "raw_copied": True,
            },
        ],
    )
    assert ev is not None
    doc = read_export_feedback(previews)
    assert doc is not None
    assert len(doc["events"]) == 1
    assert doc["events"][0]["use_session_vibe"] is True
    assert doc["events"][0]["items"][0]["file"] == "DSC0001.ARW"
    assert exported_files_aggregate(previews)["DSC0001.ARW"] == 1
