"""Read-only API for archived Agent evaluation runs."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/eval/agent", tags=["agent-eval"])

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_ARCHIVE_ROOT = _REPO_ROOT / "reports" / "agent_eval"
_LEGACY_TRACE = _REPO_ROOT / "reports" / "evaluation_trace.json"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _archive_root() -> Path:
    raw = os.getenv("LIVEHOUSE_AGENT_EVAL_REPORTS_DIR", "").strip()
    return Path(raw).expanduser().resolve() if raw else _DEFAULT_ARCHIVE_ROOT


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Agent evaluation report not found") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Agent evaluation report is unavailable") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=503, detail="Agent evaluation report is invalid")
    return value


def _run_trace(run_id: str) -> Path:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(status_code=404, detail="Agent evaluation run not found")
    root = _archive_root()
    path = (root / run_id / "evaluation_trace.json").resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Agent evaluation run not found") from exc
    return path


def _case_summary(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    case = row.get("case") if isinstance(row.get("case"), dict) else {}
    execution = row.get("execution") if isinstance(row.get("execution"), dict) else {}
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    checks = metrics.get("behavior_checks")
    failed_checks = (
        [str(name) for name, passed in checks.items() if not passed]
        if isinstance(checks, dict)
        else []
    )
    actual = metrics.get("actual_behavior") if isinstance(metrics.get("actual_behavior"), dict) else {}
    failure = metrics.get("failure") if isinstance(metrics.get("failure"), dict) else None
    quality = metrics.get("quality") if isinstance(metrics.get("quality"), dict) else {}
    return {
        "id": str(case.get("id") or ""),
        "description": str(case.get("description") or ""),
        "user_input": str(case.get("user_input") or ""),
        "passed": bool(metrics.get("passed")),
        "workflow_passed": bool(metrics.get("workflow_passed")),
        "expected_intent": (case.get("required_behavior") or {}).get("intent"),
        "actual_intent": metrics.get("actual_intent"),
        "tools": [str(tool) for tool in actual.get("tools") or []],
        "steps": int(metrics.get("steps") or 0),
        "tool_calls": int(metrics.get("tool_calls") or 0),
        "llm_calls": int(metrics.get("llm_calls") or 0),
        "latency_ms": float(execution.get("latency_ms") or 0),
        "tokens": int((execution.get("token_usage") or {}).get("total_tokens") or 0),
        "trajectory_score": metrics.get("trajectory_score"),
        "quality_score": quality.get("score"),
        "failed_checks": failed_checks,
        "failure": failure,
        "trace_id": execution.get("trace_id"),
    }


def _report_summary(report: dict[str, Any], *, include_cases: bool) -> dict[str, Any]:
    summary = {
        "schema_version": report.get("schema_version"),
        "run": report.get("run") or {},
        "metrics": report.get("metrics") or {},
        "score": report.get("score") or {},
        "thresholds": report.get("thresholds") or {},
        "threshold_failures": report.get("threshold_failures") or [],
        "regression": report.get("regression") or {},
        "passed": bool(report.get("passed")),
    }
    if include_cases:
        summary["cases"] = [_case_summary(row) for row in report.get("cases") or []]
    return summary


def _index_runs() -> list[dict[str, Any]]:
    index_path = _archive_root() / "index.json"
    if not index_path.is_file():
        return []
    value = _load_json(index_path)
    runs = value.get("runs")
    if not isinstance(runs, list):
        return []
    return [run for run in runs if isinstance(run, dict) and _RUN_ID_RE.fullmatch(str(run.get("run_id") or ""))]


def _latest_report() -> dict[str, Any]:
    runs = _index_runs()
    if runs:
        return _load_json(_run_trace(str(runs[0]["run_id"])))
    return _load_json(_LEGACY_TRACE)


@router.get("/latest")
def agent_eval_latest(include_cases: bool = Query(default=True)):
    """Latest Agent evaluation, with compact per-case summaries by default."""
    return _report_summary(_latest_report(), include_cases=include_cases)


@router.get("/runs")
def agent_eval_runs(limit: int = Query(default=30, ge=1, le=365)):
    """Newest-first daily run summaries used by dashboard trend charts."""
    runs = _index_runs()[:limit]
    if not runs and _LEGACY_TRACE.is_file():
        report = _report_summary(_load_json(_LEGACY_TRACE), include_cases=False)
        run = report["run"]
        metrics = report["metrics"]
        score = report["score"]
        runs = [
            {
                "run_id": run.get("run_id"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
                "passed": report["passed"],
                "overall_score": score.get("overall_score"),
                "behavior_success_rate": (metrics.get("behavior") or {}).get("success_rate"),
                "quality_score": (metrics.get("quality") or {}).get("quality_score"),
                "p95_latency_ms": (metrics.get("runtime") or {}).get("p95_latency_ms"),
                "model": run.get("model") or {},
                "case_count": run.get("case_count"),
            }
        ]
    return {"schema_version": "agent_evaluation_index.v1", "count": len(runs), "runs": runs}


@router.get("/runs/{run_id}")
def agent_eval_run(run_id: str, include_cases: bool = Query(default=True)):
    """One archived run without exposing raw prompts, tool payloads, or file paths."""
    return _report_summary(_load_json(_run_trace(run_id)), include_cases=include_cases)
