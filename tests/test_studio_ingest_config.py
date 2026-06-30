"""Studio ingest config persistence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils import studio_ingest_config as sic


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    path = tmp_path / "configs" / "studio_ingest.json"
    monkeypatch.setattr(sic, "ingest_config_path", lambda: path)
    monkeypatch.setattr(sic, "_default_archive_root", lambda: "/tmp/archive")
    return path


def test_save_keeps_empty_monitor_and_archive(config_file: Path) -> None:
    config_file.parent.mkdir(parents=True)
    config_file.write_text(
        json.dumps(
            {
                "ingest_monitor_path": "/old/monitor",
                "archive_root": "/old/archive",
                "session_folder_name": "",
            }
        ),
        encoding="utf-8",
    )
    out = sic.save_ingest_config(
        ingest_monitor_path="",
        archive_root="",
        session_folder_name="2026-05-24_bandX",
    )
    assert out["ingest_monitor_path"] == "/old/monitor"
    assert out["archive_root"] == "/old/archive"
    assert out["session_folder_name"] == "2026-05-24_bandX"
    assert out["session_folder_name_auto"] is False


def test_session_name_empty_means_auto(config_file: Path, tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()  # save_ingest_config validates archive_root is an existing dir
    sic.save_ingest_config(
        ingest_monitor_path="/vol/sd",
        archive_root=str(archive),
        session_folder_name="",
    )
    out = sic.read_ingest_config()
    assert out["session_folder_name"] == ""
    assert out["session_folder_name_auto"] is True
