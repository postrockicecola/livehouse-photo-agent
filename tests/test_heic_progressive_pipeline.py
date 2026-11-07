from __future__ import annotations

import json

from services.processor.pipeline_image_ops import apply_stage3_candidates_gating
from services.processor.pipeline_stage_runner import list_source_images
from services.result_service import gallery_results_revision, load_gallery_page
from utils.luma_brain import analyze_path_job_payload


def test_heic_is_discovered_and_score_all_bypasses_stage3_gating(tmp_path) -> None:
    (tmp_path / "one.HEIC").write_bytes(b"heic")
    (tmp_path / "two.jpg").write_bytes(b"jpg")
    (tmp_path / "ignore.txt").write_text("x", encoding="utf-8")
    assert list_source_images(tmp_path) == ["one.HEIC", "two.jpg"]

    rows = [{"file_name": "one.HEIC", "tech_score": 1.0, "fast_score": 1.0}]
    kept, skipped, diag = apply_stage3_candidates_gating(
        rows,
        config={
            "processing": {
                "score_all_images": True,
                "stage3_gating": {"stage3_threshold": 0.99, "max_candidates": 0},
            }
        },
    )
    assert kept == rows
    assert skipped == []
    assert diag["gating_source"] == "score_all_images"


def test_live_audit_score_overlays_heic_gallery_row(tmp_path) -> None:
    (tmp_path / "one.heic").write_bytes(b"heic")
    audit = tmp_path / "aesthetic_audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "image": "one.heic",
                "overall_score": 88.5,
                "reason": "remote score",
                "tags": ["keeper"],
                "dimensions": {"moment_peak": 9, "atmosphere_impact": 8},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    items, total, *_rest = load_gallery_page(
        str(tmp_path), "default", 0, 10, lite=True, dedupe=False
    )
    assert total == 1
    assert items[0]["file"] == "one.heic"
    assert items[0]["overall_score"] == 88.5
    assert items[0]["analysis_pending"] is False
    assert gallery_results_revision(str(tmp_path)) == audit.stat().st_mtime_ns


def test_analyze_path_payload_preserves_score_all_images() -> None:
    payload = analyze_path_job_payload(
        source_dir="/tmp/heic",
        score_all_images=True,
    )
    assert payload["score_all_images"] is True
    assert payload["enable_checkpoint"] is False


def test_lifestyle_payload_forces_all_images_and_fresh_scoring() -> None:
    payload = analyze_path_job_payload(
        source_dir="/tmp/heic",
        analysis_mode="lifestyle",
        enable_checkpoint=True,
    )
    assert payload["analysis_mode"] == "lifestyle"
    assert payload["score_all_images"] is True
    assert payload["enable_checkpoint"] is False


def test_normal_analysis_preserves_checkpoint() -> None:
    assert analyze_path_job_payload(source_dir="/tmp/photos")["enable_checkpoint"] is True


def test_analyze_switches_active_directory_and_studio_can_switch_back(tmp_path, monkeypatch) -> None:
    from unittest.mock import Mock

    from api import gallery_routes, studio_routes
    from utils import runtime_session

    old = tmp_path / "archive" / "old" / "Previews"
    requested = tmp_path / "other-archive" / "new" / "photos"
    for directory in (old, requested):
        directory.mkdir(parents=True)
        (directory / "one.jpg").write_bytes(b"jpg")
    monkeypatch.delenv("LIVEHOUSE_GALLERY_PREVIEWS_DIR", raising=False)
    monkeypatch.setattr(gallery_routes, "BASE_DIR", str(old))
    monkeypatch.setattr(gallery_routes, "_gallery_active_dir_cache", None)
    monkeypatch.setattr(gallery_routes, "_gallery_runtime_ttl_cache", None)
    # Isolate archive discovery from the developer's local DB/config.
    monkeypatch.setattr("utils.luma_brain.brain_connect", Mock(side_effect=RuntimeError))
    monkeypatch.setattr("utils.studio_ingest_config.read_ingest_config", lambda: {})
    runtime_session.write_latest_session_pointer(old)
    assert gallery_routes._runtime_base_dir() == str(old)

    monkeypatch.setattr(gallery_routes, "brain_connect", Mock())
    monkeypatch.setattr("utils.studio_sessions.find_brain_session_id", lambda *a: None)
    monkeypatch.setattr("utils.studio_sessions.find_runnable_analyze_job_id", lambda *a, **k: None)
    create_job = Mock(return_value="job-test")
    monkeypatch.setattr(gallery_routes, "create_analyze_path_job", create_job)
    monkeypatch.setattr(gallery_routes.celery_client, "send_task", Mock(return_value=Mock(id="task-test")))
    gallery_routes.enqueue_analysis(
        source_dir=str(requested), config_path="configs/livehouse.yaml", max_workers=None,
        enable_checkpoint=True, score_all_images=True, analysis_mode="livehouse", _ops=None,
    )
    assert create_job.call_args.kwargs["enable_checkpoint"] is False
    assert gallery_routes._runtime_base_dir() == str(requested)
    assert runtime_session.read_newest_latest_session_pointer(base_dir=requested)[1]["previews_dir"] == str(requested)

    # Studio activation must invalidate both caches and supersede the analysis session.
    runtime_session.write_latest_session_pointer(old)
    monkeypatch.setattr(runtime_session, "iter_candidate_archive_roots", lambda **k: [old.parent.parent, requested.parent.parent])
    import os
    old_pointer = old.parent.parent / "runtime" / "latest_session.json"
    new_pointer = requested.parent.parent / "runtime" / "latest_session.json"
    newer = new_pointer.stat().st_mtime + 1
    os.utime(old_pointer, (newer, newer))
    studio_routes._clear_gallery_runtime_cache()
    assert gallery_routes._runtime_base_dir() == str(old)
