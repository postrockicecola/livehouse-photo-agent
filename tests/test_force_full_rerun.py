"""force_full_rerun clears audit / staged / category copies before a fresh analyze."""
from __future__ import annotations

from pathlib import Path

from services.processor.pipeline_stage_runner import (
    ELIGIBLE_AFTER_S1,
    ELIGIBLE_AFTER_S2,
    PipelineStageRunner,
    staged_state_dir,
)


def test_reset_for_full_rerun_wipes_session_artifacts(tmp_path: Path):
    src = tmp_path / "Previews"
    src.mkdir()
    (src / "a.jpg").write_bytes(b"fake")
    for cat in ("best", "keep", "trash"):
        d = src / cat
        d.mkdir()
        (d / "a.jpg").write_bytes(b"copy")
    (src / "aesthetic_audit.jsonl").write_text('{"image":"a.jpg","score":90}\n', encoding="utf-8")
    (src / "analysis_results.json").write_text("[]", encoding="utf-8")
    sd = staged_state_dir(src)
    (sd / ELIGIBLE_AFTER_S1).write_text("{}\n", encoding="utf-8")
    (sd / ELIGIBLE_AFTER_S2).write_text("{}\n", encoding="utf-8")

    # Minimal config via env path override: monkeypatch runner layout fields directly.
    runner = PipelineStageRunner.__new__(PipelineStageRunner)
    runner.source_dir = src
    runner.config_path = "configs/livehouse.yaml"
    runner._config = {
        "paths": {"source_dir": str(src), "log_file": "aesthetic_audit.jsonl"},
        "folders": {"best": "best", "keep": "keep", "trash": "trash"},
    }
    runner._folders = {c: src / c for c in ("best", "keep", "trash")}
    runner._log_paths = {"log_file": src / "aesthetic_audit.jsonl"}

    out = runner.reset_for_full_rerun()
    assert out["audit"] is True
    assert out["analysis_results"] is True
    assert ELIGIBLE_AFTER_S1 in out["staged"]
    assert (src / "aesthetic_audit.jsonl").read_text(encoding="utf-8") == ""
    assert not (src / "analysis_results.json").exists()
    assert not (sd / ELIGIBLE_AFTER_S1).exists()
    assert not (src / "best" / "a.jpg").exists()
    assert (src / "a.jpg").exists()  # source preview untouched


def test_analyze_path_payload_force_disables_checkpoint():
    from utils.luma_brain import analyze_path_job_payload

    p = analyze_path_job_payload(source_dir="/tmp/x", force_full_rerun=True, enable_checkpoint=True)
    assert p["force_full_rerun"] is True
    assert p["enable_checkpoint"] is False
