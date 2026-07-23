"""Artifact content_digest CAS-ready metadata."""
from __future__ import annotations

import hashlib
from pathlib import Path

from services.job_artifacts import KIND_ANALYSIS_RESULTS
from utils.luma_brain import (
    brain_connect,
    create_job,
    list_artifacts_for_job,
    mark_job_succeeded,
    register_or_update_worker,
    update_job_status,
)


def test_primary_analysis_results_gets_digest(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMA_BRAIN_DB", str(tmp_path / "brain.db"))
    art = tmp_path / "analysis_results.json"
    body = b'{"n": 1, "ok": true}\n'
    art.write_bytes(body)
    expected = hashlib.sha256(body).hexdigest()

    conn = brain_connect()
    try:
        wid = register_or_update_worker(
            conn, worker_name="digest-test", worker_type="general", status="ONLINE", capacity=1
        )
        jid = create_job(conn, job_type="ANALYZE_PATH", max_attempts=3, trace_id="digest")
        update_job_status(conn, job_id=jid, to_status="CLAIMED", message="t")
        update_job_status(conn, job_id=jid, to_status="INFERENCING", message="t")
        mark_job_succeeded(
            conn,
            job_id=jid,
            payload={
                "worker_id": wid,
                "artifacts": [
                    {
                        "kind": KIND_ANALYSIS_RESULTS,
                        "path": str(art),
                        "generated_at": 1,
                    }
                ],
                "primary_artifact": {
                    "kind": KIND_ANALYSIS_RESULTS,
                    "path": str(art),
                },
            },
        )
        rows = list_artifacts_for_job(conn, job_id=jid)
        assert len(rows) == 1
        assert rows[0]["content_digest"] == expected
        assert Path(rows[0]["path"]).is_file()
    finally:
        conn.close()
