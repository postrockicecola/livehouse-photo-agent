from __future__ import annotations

import json
from pathlib import Path

from scripts.export_agent_eval_showcase import export_snapshots


def _report(run_id: str, score: float) -> dict:
    return {
        "schema_version": "agent_evaluation.v1",
        "run": {
            "run_id": run_id,
            "started_at": "2026-09-04T00:00:00+00:00",
            "completed_at": "2026-09-04T00:01:00+00:00",
            "model": {"provider": "ollama", "model_name": "test"},
            "case_count": 1,
        },
        "metrics": {
            "behavior": {"total": 1, "passed": 1, "success_rate": 1.0},
            "trajectory": {"trajectory_score": 100.0},
            "runtime": {"p95_latency_ms": 1000.0},
            "quality": {"quality_score": 90.0},
        },
        "score": {"overall_score": score},
        "thresholds": {"min_overall_score": 80.0},
        "threshold_failures": [],
        "regression": {"available": True, "passed": True, "regressions": []},
        "passed": True,
        "cases": [
            {
                "case": {
                    "id": "quality",
                    "description": "Quality selection",
                    "user_input": "read /Users/example/private on studio.local",
                    "required_behavior": {"intent": "quality"},
                },
                "execution": {"latency_ms": 1000, "token_usage": {"total_tokens": 42}},
                "metrics": {
                    "passed": True,
                    "workflow_passed": True,
                    "actual_intent": "quality",
                    "actual_behavior": {"tools": ["gallery_search"]},
                    "behavior_checks": {"intent": True},
                },
            }
        ],
    }


def test_export_snapshots_writes_latest_and_bounded_scrubbed_history(tmp_path: Path):
    archive = tmp_path / "archive"
    fixtures = tmp_path / "fixtures"
    older = _report("agent-eval-older", 88.0)
    latest = _report("agent-eval-latest", 92.0)

    for report in (older, latest):
        run_dir = archive / report["run"]["run_id"]
        run_dir.mkdir(parents=True)
        (run_dir / "evaluation_trace.json").write_text(json.dumps(report), encoding="utf-8")
    (archive / "index.json").write_text(
        json.dumps(
            {
                "schema_version": "agent_evaluation_index.v1",
                "runs": [latest["run"], older["run"]],
            }
        ),
        encoding="utf-8",
    )

    export_snapshots(archive, fixtures, history_limit=1)

    latest_fixture = json.loads((fixtures / "agent-eval-latest.json").read_text())
    history_fixture = json.loads((fixtures / "agent-eval-history.json").read_text())
    assert latest_fixture["run"]["run_id"] == "agent-eval-latest"
    assert latest_fixture["cases"][0]["tools"] == ["gallery_search"]
    assert "/Users/" not in latest_fixture["cases"][0]["user_input"]
    assert "studio.local" not in latest_fixture["cases"][0]["user_input"]
    assert history_fixture["count"] == 1
    assert [run["run_id"] for run in history_fixture["runs"]] == ["agent-eval-latest"]
