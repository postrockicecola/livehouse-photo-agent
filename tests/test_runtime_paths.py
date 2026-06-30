import json
from pathlib import Path

from utils.gallery_curation import read_gallery_curation, write_gallery_curation
from utils.runtime_paths import resolve_runtime_file, runtime_dir


def test_read_legacy_dot_runtime_curation(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    leg = previews / ".runtime"
    leg.mkdir(parents=True)
    (leg / "gallery_curation.json").write_text(
        '{"version":2,"selected_keys":["x.jpg"],"feedback_by_key":{"x.jpg":{"verdict":"liked"}},"export_by_file":{}}',
        encoding="utf-8",
    )
    data = read_gallery_curation(previews)
    assert data is not None
    assert "x.jpg" in data["selected_keys"]


def test_write_uses_plain_runtime_dir(tmp_path):
    previews = tmp_path / "Previews"
    previews.mkdir()
    write_gallery_curation(previews, feedback_by_key={"a.jpg": {"verdict": "liked"}})
    assert (previews / "runtime" / "gallery_curation.json").is_file()
    assert resolve_runtime_file(previews, "gallery_curation.json") == previews / "runtime" / "gallery_curation.json"


def test_latest_session_legacy_archive(tmp_path):
    archive = tmp_path / "Archive"
    leg = archive / ".runtime"
    leg.mkdir(parents=True)
    (leg / "latest_session.json").write_text(
        json.dumps({"previews_dir": "/tmp/p"}),
        encoding="utf-8",
    )
    assert resolve_runtime_file(archive, "latest_session.json") == leg / "latest_session.json"
    assert runtime_dir(archive) == archive / "runtime"
