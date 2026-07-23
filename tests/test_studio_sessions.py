"""Studio session discovery and pipeline hints."""
from __future__ import annotations

import json
from pathlib import Path

from utils.runtime_session import write_latest_session_pointer
from utils.studio_sessions import (
    _pick_preferred_analyze_job_id,
    analysis_results_ready,
    featured_frames_for_session,
    list_recent_deliveries,
    merge_session_lists,
    photography_workflow_stages,
    pipeline_view_from_job,
    scan_archive_session_dirs,
    sort_session_rows,
)


def test_analysis_results_ready_empty_array(tmp_path: Path) -> None:
    previews = tmp_path / "2026-05-01_band" / "Previews"
    previews.mkdir(parents=True)
    (previews / "analysis_results.json").write_text("[]\n", encoding="utf-8")
    assert analysis_results_ready(previews) is False
    (previews / "analysis_results.json").write_text('[{"file":"a.jpg"}]\n', encoding="utf-8")
    assert analysis_results_ready(previews) is True


def test_scan_and_merge_sessions(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    sess = archive / "2026-05-02_alpha"
    (sess / "Previews").mkdir(parents=True)
    (sess / "Previews" / "a.jpg").write_bytes(b"x")
    (sess / "RAW").mkdir()
    fs = scan_archive_session_dirs(archive)
    assert len(fs) == 1
    assert fs[0]["session_key"] == "2026-05-02_alpha"
    merged = merge_session_lists(
        fs,
        [
            {
                "session_key": "2026-05-02_alpha",
                "session_dir": str(sess),
                "previews_dir": str(sess / "Previews"),
                "preview_count": 1,
                "has_analysis_results": False,
                "brain_session_id": 9,
                "photos_ingested": 3,
                "photos_analyzed": 1,
                "source": "brain",
            }
        ],
    )
    assert merged[0]["brain_session_id"] == 9
    assert merged[0]["photos_analyzed"] == 1


def test_sort_session_rows_time_then_name() -> None:
    rows = [
        {"session_key": "2026-05-24_bandB", "session_dir": "/a"},
        {"session_key": "2026-05-24_bandA", "session_dir": "/b"},
        {"session_key": "2026-05-23", "session_dir": "/c"},
        {"session_key": "2026-05-24", "session_dir": "/d"},
    ]
    desc = [r["session_key"] for r in sort_session_rows(rows, descending=True)]
    assert desc == ["2026-05-24", "2026-05-24_bandA", "2026-05-24_bandB", "2026-05-23"]
    asc = [r["session_key"] for r in sort_session_rows(rows, descending=False)]
    assert asc == ["2026-05-23", "2026-05-24", "2026-05-24_bandA", "2026-05-24_bandB"]


def test_pipeline_view_succeeded() -> None:
    job = {"status": "SUCCEEDED", "stage_name": None}
    view = pipeline_view_from_job(job, [])
    assert view["complete"] is True
    assert view["current_index"] == len(view["labels"]) - 1


def test_photography_workflow_stages_funnel() -> None:
    funnel = {"in": 1294, "s1": 981, "s2": 500, "s3": 437, "picked": 118, "out": 118}
    stages = photography_workflow_stages(
        funnel=funnel,
        events=[],
        current_index=4,
        complete=True,
        failed=False,
    )
    assert [s["label"] for s in stages] == [
        "Imported",
        "Filtered",
        "AI Scored",
        "Picked",
        "Exported",
    ]
    assert stages[0]["count"] == 1294
    assert stages[1]["count"] == 981
    assert stages[3]["count"] == 118
    assert all(s["state"] == "done" for s in stages)


def test_photography_workflow_active_at_ai_scored() -> None:
    funnel = {"in": 100, "s1": 80, "s3": 40, "picked": 40, "out": 40}
    stages = photography_workflow_stages(
        funnel=funnel,
        events=[],
        current_index=3,
        complete=False,
        failed=False,
    )
    assert stages[2]["state"] == "active"
    assert stages[2]["label"] == "AI Scored"


def test_featured_frames_picks_three_categories(tmp_path: Path) -> None:
    previews = tmp_path / "2026-06-01_band" / "Previews"
    previews.mkdir(parents=True)
    rows = [
        {
            "file": "a.jpg",
            "path": str(previews / "a.jpg"),
            "overall_score": 90,
            "composition": 6.0,
            "energy": 5.0,
            "dimensions": {"moment_peak": 5.0, "composition_framing": 6.0},
        },
        {
            "file": "b.jpg",
            "path": str(previews / "b.jpg"),
            "overall_score": 70,
            "composition": 9.5,
            "energy": 6.0,
            "dimensions": {"moment_peak": 6.0, "composition_framing": 9.5},
        },
        {
            "file": "c.jpg",
            "path": str(previews / "c.jpg"),
            "overall_score": 75,
            "composition": 7.0,
            "energy": 9.8,
            "dimensions": {"moment_peak": 9.8, "atmosphere_impact": 8.0},
        },
    ]
    (previews / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")
    frames = featured_frames_for_session(previews)
    assert len(frames) >= 3
    labels = {f["score_label"] for f in frames[:3]}
    assert "Aesthetic" in labels
    assert "Composition" in labels
    assert "Emotion" in labels
    assert frames[0]["path_quoted"]


def test_list_recent_deliveries(tmp_path: Path) -> None:
    for day, n in (("2026-06-12", 3), ("2026-06-06", 2)):
        previews = tmp_path / day / "Previews"
        previews.mkdir(parents=True)
        rows = [{"file": f"{i}.jpg", "overall_score": 70} for i in range(n)]
        (previews / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")
    sessions = [
        {
            "session_key": "2026-06-12",
            "previews_dir": str(tmp_path / "2026-06-12" / "Previews"),
            "has_analysis_results": True,
            "preview_count": 40,
            "photos_ingested": 40,
        },
        {
            "session_key": "2026-06-06",
            "previews_dir": str(tmp_path / "2026-06-06" / "Previews"),
            "has_analysis_results": True,
            "preview_count": 12,
        },
    ]
    deliveries = list_recent_deliveries(sessions)
    assert len(deliveries) == 2
    assert deliveries[0]["session_date"] == "2026-06-12"
    assert deliveries[0]["photos_exported"] == 3
    assert deliveries[0]["photos_imported"] == 40
    assert deliveries[1]["photos_exported"] == 2
    assert deliveries[1]["photos_imported"] == 12


def test_pick_preferred_analyze_job_prefers_active_over_queued() -> None:
    assert _pick_preferred_analyze_job_id([(7, "QUEUED"), (4, "INFERENCING")]) == 4
    assert _pick_preferred_analyze_job_id([(7, "QUEUED"), (6, "QUEUED"), (5, "QUEUED")]) == 5


def test_write_latest_session_pointer(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    previews = archive / "2026-05-03_x" / "Previews"
    previews.mkdir(parents=True)
    (archive / "2026-05-03_x" / "RAW").mkdir()
    path = write_latest_session_pointer(previews)
    assert path is not None
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["previews_dir"] == str(previews.resolve())
