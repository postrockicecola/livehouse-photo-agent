"""Async curation job store, worker, and gallery skills."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.agent.curation_jobs import (
    cancel_curation_job,
    get_curation_job,
    run_curation_job,
    submit_curation_job,
)
from services.agent.skills.gallery import gallery_registry
from utils.gallery_curation import read_gallery_curation


def _write_results(base: Path) -> None:
    rows = [
        {
            "file": "drum_01.jpg",
            "overall_score": 90.0,
            "scores": {"overall": 90.0, "energy": 8.0, "technical": 8.0, "composition": 8.0},
            "category": "AI_Best_90+",
            "semantic_gate": {"status": "pass", "mode": "observe"},
            "tags": ["drummer"],
            "reason": "鼓手特写",
        },
        {
            "file": "guitar_01.jpg",
            "overall_score": 80.0,
            "scores": {"overall": 80.0, "energy": 7.0, "technical": 8.0, "composition": 7.0},
            "category": "AI_Keep_60-90",
            "semantic_gate": {"status": "pass", "mode": "observe"},
            "tags": ["guitar"],
            "reason": "吉他手",
        },
    ]
    (base / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_AGENT_DB", str(tmp_path / "agent_store.db"))
    monkeypatch.setenv("LIVEHOUSE_CURATION_JOB_BACKEND", "defer")
    _write_results(tmp_path)
    return tmp_path


def test_submit_run_writes_curation(job_env: Path) -> None:
    submitted = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 2, "query": "鼓手"},
        user_text="这场交2张给客户，偏鼓手",
    )
    assert submitted["ok"] is True
    assert submitted["status"] == "queued"
    assert submitted["deduped"] is False
    ran = run_curation_job(submitted["job_id"])
    assert ran["status"] == "done"
    assert ran["ok"] is True
    assert ran["files"]
    curation = read_gallery_curation(str(job_env)) or {}
    assert curation.get("selected_keys")


def test_dedup_active_job(job_env: Path) -> None:
    first = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 10, "query": "鼓手"},
        user_text="这场交10张给客户",
    )
    second = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 10, "query": "鼓手"},
        user_text="这场交10张给客户",
    )
    assert second["deduped"] is True
    assert second["job_id"] == first["job_id"]


def test_cancel_queued_never_writes(job_env: Path) -> None:
    submitted = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 5, "query": "鼓手"},
        user_text="这场交5张给客户",
    )
    cancelled = cancel_curation_job(submitted["job_id"], owner="anon:s1")
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    ran = run_curation_job(submitted["job_id"])
    assert ran["status"] == "cancelled"
    assert not (read_gallery_curation(str(job_env)) or {}).get("selected_keys")


def test_cancel_after_done_is_not_success(job_env: Path) -> None:
    submitted = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 2},
        user_text="这场交2张给客户",
    )
    run_curation_job(submitted["job_id"])
    cancelled = cancel_curation_job(submitted["job_id"], owner="anon:s1")
    assert cancelled["ok"] is False
    assert cancelled["status"] == "done"


def test_owner_isolation(job_env: Path) -> None:
    submitted = submit_curation_job(
        owner="anon:a",
        session_id="a",
        base_dir=str(job_env),
        goal_args={"limit": 2},
        user_text="这场交2张给客户",
    )
    assert get_curation_job(submitted["job_id"], owner="anon:b") is None
    stolen = cancel_curation_job(submitted["job_id"], owner="anon:b")
    assert stolen["ok"] is False


def test_timeout_marks_failed(job_env: Path) -> None:
    from services.agent import store

    submitted = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 2},
        user_text="这场交2张给客户",
        timeout_sec=5,
    )
    conn = store.store_connect()
    try:
        conn.execute(
            "UPDATE curation_jobs SET created_at=created_at-30, timeout_sec=1 WHERE job_id=?",
            (submitted["job_id"],),
        )
        conn.commit()
    finally:
        conn.close()
    ran = run_curation_job(submitted["job_id"])
    assert ran["status"] == "failed"
    assert ran["error"] == "timeout"


def test_skills_submit_poll_cancel(job_env: Path) -> None:
    reg = gallery_registry(str(job_env))
    submitted = reg.dispatch(
        "submit_curation_job",
        {
            "user_text": "这场交10张给客户，偏鼓手",
            "limit": 10,
            "query": "鼓手",
            "owner": "anon:s1",
            "session_id": "s1",
        },
    )
    assert submitted.ok
    job_id = submitted.metadata["job_id"]
    assert submitted.metadata["status"] == "queued"
    polled = reg.dispatch(
        "poll_curation_job",
        {"job_id": job_id, "owner": "anon:s1"},
    )
    assert polled.ok
    assert polled.metadata["status"] == "queued"
    assert "files" not in polled.metadata
    run_curation_job(job_id)
    done = reg.dispatch(
        "poll_curation_job",
        {"job_id": job_id, "owner": "anon:s1"},
    )
    assert done.metadata["status"] == "done"
    assert done.metadata.get("files")

    other = submit_curation_job(
        owner="anon:s1",
        session_id="s1",
        base_dir=str(job_env),
        goal_args={"limit": 3, "query": "吉他"},
        user_text="这场交3张给客户",
    )
    cancelled = reg.dispatch(
        "cancel_curation_job",
        {"job_id": other["job_id"], "owner": "anon:s1"},
    )
    assert cancelled.ok
    assert cancelled.metadata["status"] == "cancelled"
    assert cancelled.metadata["success"] is False
