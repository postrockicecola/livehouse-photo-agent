from __future__ import annotations

from pathlib import Path

from agent_eval.case_loader import load_cases, load_golden_cases
from agent_eval.decision_trace import build_decision_trace
from agent_eval.metrics import aggregate, composite_score, evaluate_case
from agent_eval.regression import DEFAULT_THRESHOLDS, compare


ROOT = Path(__file__).resolve().parents[1]


def _execution() -> dict:
    return {
        "reply": "已找到照片。",
        "error": None,
        "agent_trace": {"backend": "langgraph", "rounds_used": 1, "grounding_ok": True},
        "llm_calls": [{"total_tokens": 10}],
        "tool_calls": [
            {
                "tool": "gallery_search",
                "args": {"query": "鼓手"},
                "ok": True,
                "metadata": {"files": ["drum_01.jpg"]},
            }
        ],
        "tool_spans": [],
        "events": [],
        "latency_ms": 100,
        "token_usage": {"total_tokens": 10, "estimated_cost_usd": 0},
        "inference_usage": {"calls": 0},
        "runtime": {"queue_wait_ms": 0},
    }


def test_bundled_cases_load() -> None:
    cases = load_cases(ROOT / "agent_eval" / "cases")
    assert {case["id"] for case in cases} >= {
        "routed_quality_filter",
        "semantic_drummer_search",
    }
    assert all("required_behavior" in case for case in cases)
    goldens = load_golden_cases(ROOT / "agent_eval" / "golden")
    assert goldens["semantic_drummer_search"]["expected_images"] == ["drum_01.jpg"]


def test_case_scoring_covers_task_trajectory_and_ranking() -> None:
    case = {
        "id": "search",
        "description": "search",
        "user_input": "找鼓手",
        "required_behavior": {
            "intent": "langgraph",
            "final_answer": {"non_empty": True, "all_tools_ok": True},
            "selected_images": {"count": 1},
            "budgets": {"max_steps": 3, "max_tokens": 20},
        },
        "optional_behavior": {"preferred_tools": ["gallery_search"]},
        "golden": {
            "expected_images": ["drum_01.jpg"],
            "relevance": {"drum_01.jpg": 3},
            "k": 1,
        },
    }
    execution = _execution()
    metrics = evaluate_case(case, execution)
    assert metrics["passed"] is True
    assert metrics["quality"]["precision_at_k"] == 1.0
    assert metrics["quality"]["recall_at_k"] == 1.0
    report_metrics = aggregate([{"case": case, "execution": execution, "metrics": metrics}])
    assert report_metrics["behavior"]["success_rate"] == 1.0
    assert report_metrics["quality"]["map"] == 1.0
    assert report_metrics["trajectory"]["average_steps"] == 2.0
    score = composite_score(
        report_metrics,
        {"task_success": 0.4, "quality": 0.3, "cost": 0.15, "trajectory": 0.15},
    )
    assert score["overall_score"] == 100.0


def test_preferred_tools_do_not_gate_required_behavior() -> None:
    case = {
        "id": "behavior",
        "description": "implementation-independent",
        "user_input": "找鼓手",
        "required_behavior": {
            "intent": "langgraph",
            "final_answer": {"non_empty": True},
            "selected_images": {"min_count": 1},
        },
        "optional_behavior": {"preferred_tools": ["different_search_tool"]},
    }
    metrics = evaluate_case(case, _execution())
    assert metrics["workflow_passed"] is True
    assert metrics["preferred_tool_score"] == 0.0


def test_regression_detects_success_and_latency_drop() -> None:
    current = {
        "behavior": {"success_rate": 0.8},
        "trajectory": {"average_steps": 2, "average_tool_calls": 1},
        "runtime": {
            "p95_latency_ms": 150,
            "average_tokens": 100,
            "average_inference_calls": 0,
        },
        "quality": {},
    }
    baseline = {
        "metrics": {
            "behavior": {"success_rate": 1.0},
            "trajectory": {"average_steps": 2, "average_tool_calls": 1},
            "runtime": {
                "p95_latency_ms": 100,
                "average_tokens": 100,
                "average_inference_calls": 0,
            },
            "quality": {},
        }
    }
    result = compare(current, baseline, DEFAULT_THRESHOLDS)
    assert result["passed"] is False
    assert {row["category"] for row in result["regressions"]} == {
        "Behavior Regression",
        "Runtime Regression",
    }
    assert all(row["explanation"].startswith("System became worse because") for row in result["regressions"])


def test_decision_trace_contains_only_observable_summary() -> None:
    steps = build_decision_trace(
        user_input="找鼓手",
        analyzed_photo_count=200,
        planner=[
            {
                "type": "tool_decision",
                "tool": "gallery_search",
                "args": {"query": "鼓手"},
            }
        ],
        tool_spans=[
            {
                "tool": "gallery_search",
                "parameters": {"query": "鼓手"},
                "ok": True,
                "latency_ms": 5,
                "output": {
                    "ok": True,
                    "output": "returned candidates",
                    "metadata": {"count": 35, "files": ["drum_01.jpg"]},
                },
            }
        ],
        reply="找到一张。",
    )
    assert steps[0]["observation"]["analyzed_photos_available"] == 200
    assert steps[0]["action"]["tool"] == "gallery_search"
    assert steps[0]["result"]["result_count"] == 35
    assert "thought" not in steps[0]


def test_failure_taxonomy_distinguishes_invalid_tool_parameters() -> None:
    execution = _execution()
    execution["tool_calls"][0]["ok"] = False
    execution["tool_calls"][0]["metadata"]["error"] = "invalid required argument: query"
    case = {
        "id": "bad_args",
        "description": "bad args",
        "user_input": "找鼓手",
        "required_behavior": {
            "intent": "langgraph",
            "final_answer": {"all_tools_ok": True},
        },
        "optional_behavior": {},
    }
    metrics = evaluate_case(case, execution)
    assert metrics["failure"] == {
        "category": "Tool Parameter Error",
        "subtype": "invalid_arguments",
        "reason": "invalid required argument: query",
    }

