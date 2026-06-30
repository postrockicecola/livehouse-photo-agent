"""Integration test: the curation agent wired into the SSOT job system.

Verifies that ``run_curation_job`` streams the agent's decisions into ``job_events``
(so the Infra Console timeline renders the loop), writes a ``curation_result.json``
artifact, and returns a metrics + selection summary — all with a fake analysis
backend, an in-memory DB, and image files that are never decoded.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from services.agent.job_runner import run_curation_job
from utils.stage3_dimensions import STAGE3_DIM_KEYS

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "luma_brain_schema.sql").read_text(encoding="utf-8")


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _make_job(conn: sqlite3.Connection, *, source_dir: str) -> int:
    from utils.luma_brain import create_curate_path_job

    return create_curate_path_job(
        conn,
        source_dir=source_dir,
        agent={"target_keepers": 2, "max_inferences": 10, "allow_escalation": True},
        trace_id="t-curate-1",
    )


def _seed_images(tmp_path: Path, names: list[str]) -> None:
    for n in names:
        (tmp_path / n).write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")


def _fake_analyze(score_by_id: dict[str, float]):
    def _fn(image_path: str, tier: str) -> dict:
        cid = Path(image_path).name
        conf = 0.6 if tier == "fast" else 0.95  # fast is shaky -> triggers escalation
        return {
            "score": score_by_id.get(cid, 50.0),
            "confidence": conf,
            "dimensions": {k: 7 for k in STAGE3_DIM_KEYS},
            "verdict": f"{tier} verdict",
        }

    return _fn


def test_run_curation_job_writes_events_and_artifact(tmp_path):
    names = ["a.jpg", "b.jpg", "c.jpg"]
    _seed_images(tmp_path, names)
    conn = _conn()
    job_id = _make_job(conn, source_dir=str(tmp_path))

    summary = run_curation_job(
        conn,
        job_id=job_id,
        source_dir=str(tmp_path),
        trace_id="t-curate-1",
        analyze_fn=_fake_analyze({"a.jpg": 92.0, "b.jpg": 81.0, "c.jpg": 40.0}),
    )

    # summary surface
    assert summary["candidate_count"] == 3
    assert summary["selected"] == ["a.jpg", "b.jpg"]
    assert summary["metrics"]["selected_count"] == 2
    assert summary["metrics"]["escalations"] >= 1

    # artifact written
    art = Path(summary["curation_artifact"])
    assert art.exists()
    body = json.loads(art.read_text(encoding="utf-8"))
    assert body["selected"] == ["a.jpg", "b.jpg"]
    assert len(body["candidates"]) == 3

    # job_events streamed (committed) for analyze + finalize, not inspect
    rows = conn.execute(
        "SELECT message, payload_json FROM job_events WHERE job_id = ? ORDER BY id ASC",
        (job_id,),
    ).fetchall()
    actions = [json.loads(r["payload_json"]).get("agent_action") for r in rows if r["payload_json"]]
    assert "analyze" in actions
    assert actions.count("finalize") == 1
    assert "inspect" not in actions  # cheap steps are intentionally not logged
    # at least one escalated analyze event recorded
    assert any(
        json.loads(r["payload_json"]).get("source") == "reflection"
        for r in rows
        if r["payload_json"]
    )


def test_run_curation_job_no_images_raises(tmp_path):
    conn = _conn()
    job_id = _make_job(conn, source_dir=str(tmp_path))
    try:
        run_curation_job(
            conn,
            job_id=job_id,
            source_dir=str(tmp_path),
            trace_id="t-curate-1",
            analyze_fn=_fake_analyze({}),
        )
        assert False, "expected FileNotFoundError for empty source dir"
    except FileNotFoundError:
        pass
