from __future__ import annotations

import json
from pathlib import Path

from api import eval_routes


def _report(run_id: str = "agent-eval-test") -> dict:
    return {
        "schema_version": "agent_evaluation.v1",
        "run": {
            "run_id": run_id,
            "started_at": "2026-08-29T00:00:00+00:00",
            "completed_at": "2026-08-29T00:01:00+00:00",
            "model": {"provider": "ollama", "model_name": "test"},
            "case_count": 1,
        },
        "metrics": {
            "behavior": {"total": 1, "passed": 1, "success_rate": 1.0},
            "trajectory": {"trajectory_score": 100.0},
            "runtime": {"p95_latency_ms": 1000.0},
            "quality": {"quality_score": 90.0},
        },
        "score": {"overall_score": 95.0},
        "thresholds": {"min_overall_score": 80.0},
        "threshold_failures": [],
        "regression": {"available": True, "passed": True, "regressions": []},
        "passed": True,
        "cases": [
            {
                "case": {
                    "id": "quality",
                    "description": "Quality selection",
                    "user_input": "pick",
                    "required_behavior": {"intent": "quality"},
                },
                "execution": {
                    "trace_id": "trace-1",
                    "latency_ms": 1000,
                    "token_usage": {"total_tokens": 42},
                },
                "metrics": {
                    "passed": True,
                    "workflow_passed": True,
                    "actual_intent": "quality",
                    "actual_behavior": {"tools": ["gallery_search"]},
                    "behavior_checks": {"intent": True},
                    "steps": 2,
                    "tool_calls": 1,
                    "llm_calls": 1,
                    "trajectory_score": 100,
                    "quality": {"score": 90},
                },
            }
        ],
    }


def test_latest_report_uses_archive_and_returns_compact_cases(tmp_path: Path, monkeypatch):
    report = _report()
    run_dir = tmp_path / report["run"]["run_id"]
    run_dir.mkdir()
    (run_dir / "evaluation_trace.json").write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "index.json").write_text(
        json.dumps({"schema_version": "agent_evaluation_index.v1", "runs": [report["run"]]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("LIVEHOUSE_AGENT_EVAL_REPORTS_DIR", str(tmp_path))

    latest = eval_routes.agent_eval_latest(include_cases=True)

    assert latest["run"]["run_id"] == "agent-eval-test"
    assert latest["cases"][0]["tools"] == ["gallery_search"]
    assert "execution" not in latest["cases"][0]


def test_run_id_cannot_escape_archive(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_AGENT_EVAL_REPORTS_DIR", str(tmp_path))

    try:
        eval_routes.agent_eval_run("../../outside")
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("unsafe run id unexpectedly accepted")
