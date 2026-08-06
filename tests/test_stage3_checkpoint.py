"""Stage3 resume: skip real audit rows, re-score rate-limit / crash placeholders."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List

from services.processor.pipeline_image_ops import append_aesthetic_audit_line
from services.processor.pipeline_stage_runner import (
    ELIGIBLE_AFTER_S2,
    PipelineStageRunner,
    audit_entry_is_retryable,
    staged_state_dir,
    stats_from_audit_log,
)

CATEGORIES = ("best", "keep", "trash")


def _layout(tmp_path: Path) -> Path:
    src = tmp_path / "Previews"
    src.mkdir()
    for cat in CATEGORIES:
        (src / cat).mkdir()
    return src


def _runner(src: Path) -> PipelineStageRunner:
    r = PipelineStageRunner.__new__(PipelineStageRunner)
    r.source_dir = src
    r.config_path = "configs/livehouse.yaml"
    r.trace_id = "trace-test"
    r.job_id = None
    r.session_id = None
    r.worker_id = 0
    r.file_lock = Lock()
    r._config = {"processing": {"max_workers": 1}}
    r._folders = {c: src / c for c in CATEGORIES}
    r._log_paths = {"log_file": src / "aesthetic_audit.jsonl"}
    r._pipe = _StubPipe(r._config)
    r._pt_resolved = True
    r._pt_session = None
    return r


class _StubPipe:
    """Stand-in for AestheticPipeline so Stage3 can be entered without a VLM client."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config


def _write_audit(src: Path, rows: List[Dict[str, Any]]) -> None:
    (src / "aesthetic_audit.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def _scored(name: str, score: float = 88.0) -> Dict[str, Any]:
    return {"image": name, "file_name": name, "score": score, "tags": ["keeper"]}


def _rate_limited(name: str) -> Dict[str, Any]:
    return {"image": name, "file_name": name, "score": 30.0, "tags": ["vlm_error"]}


def _write_stage2_manifest(src: Path, names: List[str]) -> None:
    rows = [{"file_name": n, "tech_score": 70.0, "debug_info": {}} for n in names]
    (staged_state_dir(src) / ELIGIBLE_AFTER_S2).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )


def test_retryable_tags_detected():
    assert audit_entry_is_retryable(_rate_limited("a.jpg")) is True
    assert audit_entry_is_retryable({"tags": ["pipeline_error"]}) is True
    assert audit_entry_is_retryable(_scored("a.jpg")) is False
    assert audit_entry_is_retryable({"tags": ["technical_issue"]}) is False
    assert audit_entry_is_retryable({}) is False


def test_done_names_exclude_vlm_fallback_rows(tmp_path: Path):
    src = _layout(tmp_path)
    _write_audit(
        src,
        [
            _scored("a.jpg"),
            _rate_limited("b.jpg"),
            {"image": "c.jpg", "file_name": "c.jpg", "score": 0.0, "tags": ["pipeline_error"]},
            {"image": "d.jpg", "file_name": "d.jpg", "score": 12.0, "tags": ["technical_issue"]},
        ],
    )
    done = _runner(src)._audit_logged_image_names()
    assert done == {"a.jpg", "d.jpg"}  # stage1 rejects stay done; placeholders do not


def test_done_names_last_row_wins_after_successful_retry(tmp_path: Path):
    src = _layout(tmp_path)
    _write_audit(src, [_rate_limited("a.jpg"), _scored("a.jpg")])
    assert _runner(src)._audit_logged_image_names() == {"a.jpg"}


def test_stage3_skips_images_with_real_scores(tmp_path: Path):
    src = _layout(tmp_path)
    _write_stage2_manifest(src, ["a.jpg", "b.jpg"])
    _write_audit(src, [_scored("a.jpg"), _scored("b.jpg")])

    out = _runner(src).run_stage3_vlm(max_workers=1, conn=None)
    assert out["checkpoint_skipped"] == 2
    assert out["total_in"] == 0
    assert out["processed"] == 0


def _run_stage3_without_inference(runner: PipelineStageRunner, monkeypatch, **kwargs):
    """Enter Stage3 for its planning / checkpoint logic, then degrade out of the VLM work."""
    monkeypatch.setattr(
        "services.processor.pipeline_stage_runner.should_run_stage3", lambda _d: False
    )
    return runner.run_stage3_vlm(max_workers=1, conn=None, **kwargs)


def test_stage3_retries_rate_limited_image(tmp_path: Path, monkeypatch):
    src = _layout(tmp_path)
    _write_stage2_manifest(src, ["a.jpg", "b.jpg"])
    _write_audit(src, [_scored("a.jpg"), _rate_limited("b.jpg")])

    out = _run_stage3_without_inference(_runner(src), monkeypatch)
    assert out["checkpoint_skipped"] == 1  # a.jpg only
    assert out["total_in"] == 1  # b.jpg still a candidate


def test_stage3_checkpoint_disabled_keeps_full_manifest(tmp_path: Path, monkeypatch):
    src = _layout(tmp_path)
    _write_stage2_manifest(src, ["a.jpg", "b.jpg"])
    _write_audit(src, [_scored("a.jpg"), _scored("b.jpg")])

    out = _run_stage3_without_inference(_runner(src), monkeypatch, enable_checkpoint=False)
    assert out["checkpoint_skipped"] == 0
    assert out["total_in"] == 2


def test_stats_count_each_image_once_after_retry(tmp_path: Path):
    src = _layout(tmp_path)
    _write_audit(src, [_rate_limited("a.jpg"), _scored("a.jpg"), _scored("b.jpg")])
    stats = stats_from_audit_log(src / "aesthetic_audit.jsonl")
    assert stats["processed"] == 2
    assert stats["vlm_fallback"] == 0


def test_rescored_image_leaves_one_category_copy(tmp_path: Path):
    src = _layout(tmp_path)
    (src / "a.jpg").write_bytes(b"fake")
    folders = {c: src / c for c in CATEGORIES}
    log_paths = {"log_file": src / "aesthetic_audit.jsonl"}
    common = {
        "config": {},
        "folders": folders,
        "log_paths": log_paths,
        "file_lock": None,
        "image_path": str(src / "a.jpg"),
    }

    append_aesthetic_audit_line(**common, ai_data={"score": 30.0, "tags": ["vlm_error"]})
    assert (src / "trash" / "a.jpg").exists()

    append_aesthetic_audit_line(**common, ai_data={"score": 95.0, "tags": ["keeper"]})
    assert (src / "best" / "a.jpg").exists()
    assert not (src / "trash" / "a.jpg").exists()
