"""Studio analyze-bulk: one job per session, no active-session flip."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def archive_with_sessions(tmp_path: Path) -> Path:
    archive = tmp_path / "Livehouse_Archive"
    for key, n_jpg in (("2026-05-01_a", 2), ("2026-05-02_b", 1), ("2026-05-03_empty", 0)):
        previews = archive / key / "Previews"
        previews.mkdir(parents=True)
        for i in range(n_jpg):
            (previews / f"p{i}.jpg").write_bytes(b"x")
    return archive


def _client(monkeypatch: pytest.MonkeyPatch, archive: Path) -> TestClient:
    from api import studio_routes

    monkeypatch.setenv("LUMA_ARCHIVE_ROOT", str(archive))

    class _Task:
        id = "celery-task-1"

    monkeypatch.setattr(studio_routes._celery, "send_task", MagicMock(return_value=_Task()))

    job_ids = {"n": 100}

    def fake_create_analyze_path_job(conn: Any, **kwargs: Any) -> int:
        _ = conn
        job_ids["n"] += 1
        return int(job_ids["n"])

    monkeypatch.setattr(studio_routes, "brain_connect", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(studio_routes, "find_brain_session_id", MagicMock(return_value=None))
    monkeypatch.setattr(studio_routes, "find_runnable_analyze_job_id", MagicMock(return_value=None))
    monkeypatch.setattr(studio_routes, "create_analyze_path_job", fake_create_analyze_path_job)
    monkeypatch.setattr(studio_routes, "create_job", MagicMock(side_effect=AssertionError("should use path job")))
    monkeypatch.setattr(studio_routes, "write_latest_session_pointer", MagicMock(return_value=archive / "runtime" / "x.json"))
    monkeypatch.setattr(studio_routes, "_clear_gallery_runtime_cache", MagicMock())

    app = FastAPI()
    app.include_router(studio_routes.router)
    return TestClient(app)


def test_analyze_bulk_queues_one_job_per_nonempty_session(
    monkeypatch: pytest.MonkeyPatch, archive_with_sessions: Path
) -> None:
    from api import studio_routes

    client = _client(monkeypatch, archive_with_sessions)
    res = client.post("/api/studio/analyze-bulk", json={"force_full_rerun": True})
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["started_count"] == 2
    assert data["skipped_count"] == 1
    assert data["skipped"][0]["reason"] == "empty_previews"
    assert {x["session_key"] for x in data["started"]} == {"2026-05-01_a", "2026-05-02_b"}
    assert studio_routes._celery.send_task.call_count == 2
    # Bulk must not flip the active gallery session.
    studio_routes.write_latest_session_pointer.assert_not_called()


def test_analyze_bulk_skips_already_running(
    monkeypatch: pytest.MonkeyPatch, archive_with_sessions: Path
) -> None:
    from api import studio_routes

    client = _client(monkeypatch, archive_with_sessions)
    monkeypatch.setattr(studio_routes, "find_runnable_analyze_job_id", MagicMock(return_value=42))
    res = client.post("/api/studio/analyze-bulk", json={"force_full_rerun": True})
    assert res.status_code == 200
    data = res.json()
    assert data["started_count"] == 0
    assert data["already_running_count"] == 2
    assert data["already_running"][0]["job_id"] == 42
    studio_routes._celery.send_task.assert_not_called()
