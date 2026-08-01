"""L1 live-eval scorer + mock suite gate (no network)."""
from __future__ import annotations

from scripts.eval.agent_case_score import aggregate_scores, score_case
from scripts.eval.eval_agent_live import evaluate, select_live_cases


def test_select_live_suites() -> None:
    smoke = select_live_cases(suite="smoke")
    core = select_live_cases(suite="core")
    assert len(smoke) >= 10
    assert all(c.get("live") for c in smoke)
    assert len(core) >= len(smoke)
    assert all(c.get("live") for c in core)


def test_score_case_live_relaxes_pref_key() -> None:
    case = {
        "id": "x",
        "expect": {"tools": ["remember_preference"], "pref_key": "avoid_silhouettes"},
    }
    row = score_case(
        case=case,
        reply="已记住偏好。",
        tool_calls=[{"tool": "remember_preference", "args": {"key": "avoid_silhouettes", "value": "true"}, "metadata": {}}],
        working_memory={},
        prefs={"avoid_silhouettes": "true"},
        backend="langgraph",
        elapsed_ms=10,
        live=True,
    )
    assert row["ok"]


def test_aggregate_pass_at_1() -> None:
    rows = [
        {"ok": True, "tool_name_acc": 1.0, "route_ok": True, "json_leak": False, "grounded_ok": True, "empty_honest": True, "allow_empty": False, "tool_calls": 1, "elapsed_ms": 10},
        {"ok": False, "tool_name_acc": 0.5, "route_ok": False, "json_leak": True, "grounded_ok": True, "empty_honest": True, "allow_empty": False, "tool_calls": 2, "elapsed_ms": 20},
    ]
    m = aggregate_scores(rows)
    assert m["total"] == 2
    assert m["passed"] == 1
    assert m["pass_at_1"] == 0.5
    assert m["json_leak_rate"] == 0.5


def test_eval_agent_live_mock_smoke() -> None:
    report = evaluate(suite="smoke", mode="mock")
    assert report["mode"] == "mock"
    assert report["prompt_hash"]
    assert report["metrics"]["total"] >= 5
    assert report["metrics"]["passed"] == report["metrics"]["total"], [
        c for c in report["cases"] if not c["ok"]
    ]
