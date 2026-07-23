"""Focused JobExecutor claim branches (isolated SQLite)."""
from __future__ import annotations

from unittest.mock import patch

from services.job_executor import JobExecutor
from utils.luma_brain import brain_connect, create_job, update_job_status


def test_claim_skipped_when_job_not_runnable(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    conn = brain_connect()
    try:
        jid = create_job(conn, job_type="ANALYZE_PATH", max_attempts=3, trace_id="t-skip")
        update_job_status(conn, job_id=jid, to_status="CLAIMED", message="preclaimed")
    finally:
        conn.close()

    out = JobExecutor().run(jid)
    assert out["ok"] is False
    assert out["claimed"] is False
    assert out["message"] == "job not runnable or missing"


def test_admission_denied_branch(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    conn = brain_connect()
    try:
        jid = create_job(conn, job_type="ANALYZE_PATH", max_attempts=3, trace_id="t-admit")
    finally:
        conn.close()

    with patch("services.job_lifecycle.JobLifecycle.claim", return_value=(None, "worker_admission:PAUSED")):
        out = JobExecutor().run(jid)
    assert out["ok"] is False
    assert out["claimed"] is False
    assert "not accepting" in out["message"]
    assert out["admission"] == "worker_admission:PAUSED"
