"""Behavior, trajectory, runtime, golden-ranking, and composite Agent metrics."""
from __future__ import annotations

import math
from typing import Any

from agent_eval.case_loader import normalize_case
from scripts.eval.metrics import mae, spearman


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 3)


def selected_images(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Return the last observable candidate/selection set emitted by a skill."""
    selected: list[str] = []
    for call in tool_calls:
        meta = call.get("metadata") or {}
        for key in ("selected_keys", "files"):
            values = meta.get(key)
            if isinstance(values, list) and values:
                selected = [str(value) for value in values]
    return list(dict.fromkeys(selected))


def _selection_updated(tool_calls: list[dict[str, Any]]) -> bool:
    return any(
        (call.get("metadata") or {}).get("ui_action") == "reload_curation"
        or "selected_keys" in (call.get("metadata") or {})
        for call in tool_calls
        if call.get("ok")
    )


def _result_ok(expected: Any, reply: str, tool_calls: list[dict[str, Any]]) -> bool:
    if isinstance(expected, str):
        return expected.lower() in reply.lower()
    if not isinstance(expected, dict):
        return True
    low = reply.lower()
    if expected.get("non_empty") and not reply.strip():
        return False
    if expected.get("all_tools_ok") and any(not call.get("ok") for call in tool_calls):
        return False
    if any(str(item).lower() not in low for item in expected.get("contains_all", [])):
        return False
    contains_any = [str(item).lower() for item in expected.get("contains_any", [])]
    if contains_any and not any(item in low for item in contains_any):
        return False
    if any(str(item).lower() in low for item in expected.get("not_contains", [])):
        return False
    return True


def _actual_intent(trace: dict[str, Any]) -> str:
    return str(trace.get("rule_id") or trace.get("backend") or "unknown")


def _matches(expected: Any, actual: str) -> bool:
    if isinstance(expected, list):
        return actual in {str(item) for item in expected}
    return actual == str(expected)


def _preferred_tool_score(preferred: list[str], actual: list[str]) -> float | None:
    if not preferred:
        return None
    hits = sum(1 for tool in preferred if tool in actual)
    return round(hits / len(preferred), 4)


def _ranking(
    expected: list[str],
    actual: list[str],
    *,
    k: int,
    relevance: dict[str, Any] | None = None,
) -> dict[str, float | None]:
    expected = list(dict.fromkeys(expected))
    actual = list(dict.fromkeys(actual))
    kk = min(max(1, int(k)), len(actual)) if actual else 0
    top = actual[:kk]
    relevant = set(expected)
    hits = len(relevant.intersection(top))
    precision = hits / kk if kk else (1.0 if not expected else 0.0)
    recall = hits / len(relevant) if relevant else 1.0
    gains = {
        image: float((relevance or {}).get(image, len(expected) - rank))
        for rank, image in enumerate(expected)
    }
    dcg = sum(gains.get(image, 0.0) / math.log2(rank + 2) for rank, image in enumerate(top))
    ideal = sorted(gains.values(), reverse=True)[:kk]
    idcg = sum(value / math.log2(rank + 2) for rank, value in enumerate(ideal))
    precision_sum = 0.0
    relevant_seen = 0
    for rank, image in enumerate(top, start=1):
        if image in relevant:
            relevant_seen += 1
            precision_sum += relevant_seen / rank
    ap_denom = min(len(relevant), max(1, int(k)))
    average_precision = precision_sum / ap_denom if ap_denom else 1.0
    reciprocal_rank = next(
        (1.0 / rank for rank, image in enumerate(top, start=1) if image in relevant),
        0.0,
    )
    return {
        "precision_at_k": round(precision, 4),
        "recall_at_k": round(recall, 4),
        "ndcg": round(dcg / idcg, 4) if idcg else None,
        "average_precision": round(average_precision, 4),
        "reciprocal_rank": round(reciprocal_rank, 4),
    }


def _score_pairs(
    case: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> tuple[list[float], list[float]]:
    expected = case.get("expected_scores")
    if not isinstance(expected, dict):
        return [], []
    actual: dict[str, float] = {}
    for call in tool_calls:
        for row in (call.get("metadata") or {}).get("rows") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("file") or row.get("filename") or "")
            score = row.get("overall_score", row.get("score"))
            if name and isinstance(score, (int, float)):
                actual[name] = float(score)
    keys = [str(key) for key in expected if str(key) in actual]
    return [float(expected[key]) for key in keys], [actual[key] for key in keys]


def _check_count(spec: dict[str, Any], actual: int) -> bool:
    if "count" in spec and actual != int(spec["count"]):
        return False
    if "min_count" in spec and actual < int(spec["min_count"]):
        return False
    if "max_count" in spec and actual > int(spec["max_count"]):
        return False
    return True


def _budget_values(execution: dict[str, Any], steps: int, tool_count: int) -> dict[str, float]:
    return {
        "max_steps": float(steps),
        "max_tool_calls": float(tool_count),
        "max_llm_calls": float(len(execution.get("llm_calls") or [])),
        "max_inference_calls": float(
            (execution.get("inference_usage") or {}).get("calls") or 0
        ),
        "max_tokens": float((execution.get("token_usage") or {}).get("total_tokens") or 0),
        "max_latency_ms": float(execution.get("latency_ms") or 0),
    }


def _efficiency_score(actual: float, target: float | None) -> float | None:
    if target is None:
        return None
    if actual <= target:
        return 100.0
    return round(max(0.0, target / max(actual, 1.0) * 100.0), 2)


def _failure(
    *,
    execution: dict[str, Any],
    checks: dict[str, bool],
    tools: list[str],
    optional: dict[str, Any],
    loop_detected: bool,
    retries: int,
    quality_passed: bool | None,
) -> dict[str, str] | None:
    error = str(execution.get("error") or "")
    tool_calls = execution.get("tool_calls") or []
    execution_spans = execution.get("tool_spans") or []
    failed_calls = [call for call in [*tool_calls, *execution_spans] if not call.get("ok")]
    tool_error = " ".join(
        str(call.get("error") or (call.get("metadata") or {}).get("error") or "")
        for call in failed_calls
    )
    low_error = f"{error} {tool_error}".lower()
    if any(not ok for name, ok in checks.items() if name.startswith("budget.")):
        return {
            "category": "Budget Failure",
            "subtype": "budget_exceeded",
            "reason": "One or more token/tool/inference/latency budgets were exceeded.",
        }
    if any(token in low_error for token in ("invalid", "argument", "required", "missing")):
        return {
            "category": "Tool Parameter Error",
            "subtype": "invalid_arguments",
            "reason": tool_error or error or "Tool rejected its arguments.",
        }
    if error or failed_calls:
        return {
            "category": "Execution Failure",
            "subtype": "api_database_or_model_failure",
            "reason": error or tool_error or "A runtime dependency failed.",
        }
    if any(name.startswith("memory.") and not ok for name, ok in checks.items()):
        return {
            "category": "Memory Failure",
            "subtype": "wrong_context_retrieval",
            "reason": "Required memory state was not available or was not updated.",
        }
    forbidden = set(optional.get("_forbidden_tools") or [])
    if forbidden.intersection(tools):
        return {
            "category": "Tool Selection Error",
            "subtype": "wrong_skill",
            "reason": f"Forbidden tools were selected: {sorted(forbidden.intersection(tools))}.",
        }
    if retries and not all(checks.values()):
        return {
            "category": "Reflection Failure",
            "subtype": "failed_to_recover",
            "reason": "The agent retried or repaired output but did not recover.",
        }
    if loop_detected or any(
        not ok for name, ok in checks.items() if name in {"intent", "final_answer", "no_loop"}
    ):
        return {
            "category": "Planning Error",
            "subtype": "unnecessary_steps" if loop_detected else "wrong_decomposition",
            "reason": "The plan looped or did not satisfy the required behavior.",
        }
    if quality_passed is False:
        return {
            "category": "Ranking Failure",
            "subtype": "selected_wrong_images",
            "reason": "The workflow completed, but selected images missed the photography golden.",
        }
    if not all(checks.values()):
        return {
            "category": "Unknown",
            "subtype": "behavior_contract_failed",
            "reason": "A required behavior was not observed.",
        }
    return None


def evaluate_case(case: dict[str, Any], execution: dict[str, Any]) -> dict[str, Any]:
    """Score workflow correctness independently from photography quality."""
    case = normalize_case(case)
    required = dict(case.get("required_behavior") or {})
    optional = dict(case.get("optional_behavior") or {})
    trace = execution.get("agent_trace") or {}
    tool_calls = execution.get("tool_calls") or []
    tools = [str(call.get("tool") or "") for call in tool_calls]
    selected = selected_images(tool_calls)
    llm_calls = len(execution.get("llm_calls") or [])
    inference_calls = int((execution.get("inference_usage") or {}).get("calls") or 0)
    steps = llm_calls + len(tool_calls)
    signatures = [
        (call.get("tool"), repr(sorted((call.get("args") or {}).items())))
        for call in tool_calls
    ]
    loop_detected = len(signatures) != len(set(signatures))
    retries = sum(
        1
        for event in execution.get("events", [])
        if event.get("type") in {"parse_repair", "retry"}
    )

    checks: dict[str, bool] = {"execution": not bool(execution.get("error"))}
    actual_intent = _actual_intent(trace)
    if "intent" in required:
        checks["intent"] = _matches(required["intent"], actual_intent)
    if "final_answer" in required:
        checks["final_answer"] = _result_ok(
            required["final_answer"], execution.get("reply") or "", tool_calls
        )
    selection_spec = required.get("selected_images")
    if isinstance(selection_spec, dict):
        checks["selected_images.count"] = _check_count(selection_spec, len(selected))
    state = required.get("state_changes")
    if isinstance(state, dict) and "gallery_selection_updated" in state:
        checks["state.gallery_selection_updated"] = (
            _selection_updated(tool_calls) is bool(state["gallery_selection_updated"])
        )
    memory_spec = required.get("memory")
    if isinstance(memory_spec, dict):
        changed = execution.get("memory_changes") or {}
        changed_keys = set((changed.get("working_memory") or {})) | set(
            (changed.get("preferences") or {})
        )
        for key in memory_spec.get("required_keys") or []:
            checks[f"memory.{key}"] = str(key) in changed_keys
    forbidden_tools = [str(tool) for tool in required.get("forbidden_tools") or []]
    if forbidden_tools:
        checks["tools.forbidden"] = not set(forbidden_tools).intersection(tools)
        optional["_forbidden_tools"] = forbidden_tools
    checks["no_loop"] = not loop_detected

    actual_budgets = _budget_values(execution, steps, len(tool_calls))
    budgets = dict(required.get("budgets") or {})
    for key, limit in budgets.items():
        if key in actual_budgets:
            checks[f"budget.{key}"] = actual_budgets[key] <= float(limit)
    workflow_passed = all(checks.values())

    preferred_tools = [str(tool) for tool in optional.get("preferred_tools") or []]
    preferred_tool_score = _preferred_tool_score(preferred_tools, tools)
    trajectory_scores = [
        score
        for score in (
            _efficiency_score(
                steps,
                float(optional.get("target_steps"))
                if optional.get("target_steps") is not None
                else float(budgets["max_steps"])
                if "max_steps" in budgets
                else None,
            ),
            _efficiency_score(
                len(tool_calls),
                float(optional.get("target_tool_calls"))
                if optional.get("target_tool_calls") is not None
                else float(budgets["max_tool_calls"])
                if "max_tool_calls" in budgets
                else None,
            ),
            100.0 if not loop_detected else 0.0,
        )
        if score is not None
    ]
    trajectory_score = _mean(trajectory_scores)
    cost_scores = [
        score
        for score in (
            _efficiency_score(actual_budgets["max_tokens"], budgets.get("max_tokens")),
            _efficiency_score(
                actual_budgets["max_inference_calls"], budgets.get("max_inference_calls")
            ),
        )
        if score is not None
    ]
    cost_score = _mean(cost_scores)

    golden = case.get("golden")
    quality: dict[str, Any] = {"eligible": False}
    quality_passed: bool | None = None
    if isinstance(golden, dict):
        expected_images = [str(item) for item in golden.get("expected_images") or []]
        quality = {
            "eligible": True,
            "expected_images": expected_images,
            "actual_images": selected,
            **_ranking(
                expected_images,
                selected,
                k=int(golden.get("k") or len(expected_images) or 10),
                relevance=dict(golden.get("relevance") or {}),
            ),
        }
        quality_passed = float(quality["recall_at_k"] or 0) >= float(
            golden.get("min_recall", 1.0)
        )
        quality["passed"] = quality_passed
        quality["score"] = round(
            _mean(
                [
                    float(quality[key]) * 100
                    for key in (
                        "precision_at_k",
                        "recall_at_k",
                        "ndcg",
                        "average_precision",
                    )
                    if quality.get(key) is not None
                ]
            )
            or 0.0,
            2,
        )
    human_scores, model_scores = _score_pairs(case, tool_calls)
    quality["spearman"] = (
        round(spearman(human_scores, model_scores), 4)
        if len(human_scores) >= 2
        else None
    )
    quality["mae"] = round(mae(human_scores, model_scores), 4) if human_scores else None

    failure = _failure(
        execution=execution,
        checks=checks,
        tools=tools,
        optional=optional,
        loop_detected=loop_detected,
        retries=retries,
        quality_passed=quality_passed,
    )
    return {
        "passed": workflow_passed,
        "workflow_passed": workflow_passed,
        "behavior_checks": checks,
        "actual_behavior": {
            "intent": actual_intent,
            "selected_images_count": len(selected),
            "gallery_selection_updated": _selection_updated(tool_calls),
            "tools": tools,
        },
        "actual_intent": actual_intent,
        "intent_ok": checks.get("intent"),
        "result_ok": checks.get("final_answer", True),
        "preferred_tool_score": preferred_tool_score,
        "selected_images": selected,
        "steps": steps,
        "tool_calls": len(tool_calls),
        "llm_calls": llm_calls,
        "inference_calls": inference_calls,
        "reflection_count": sum(
            1 for event in execution.get("events", []) if event.get("type") == "reflection"
        ),
        "retry_count": retries,
        "loop_detected": loop_detected,
        "max_depth": steps,
        "trajectory_score": trajectory_score,
        "cost_score": cost_score,
        "quality": quality,
        "quality_passed": quality_passed,
        "failure": failure,
        "failure_classification": failure.get("category") if failure else None,
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row["metrics"] for row in results]
    executions = [row["execution"] for row in results]
    n = len(scored)
    latencies = [float(row.get("latency_ms") or 0) for row in executions]
    token_counts = [
        float((row.get("token_usage") or {}).get("total_tokens") or 0)
        for row in executions
    ]
    quality_rows = [row["quality"] for row in scored if row["quality"].get("eligible")]
    intent_rows = [row for row in scored if row.get("intent_ok") is not None]
    behavior = {
        "total": n,
        "passed": sum(1 for row in scored if row["workflow_passed"]),
        "success_rate": _mean(
            [1.0 if row["workflow_passed"] else 0.0 for row in scored]
        ),
        "intent_accuracy": _mean(
            [1.0 if row["intent_ok"] else 0.0 for row in intent_rows]
        ),
        "preferred_tool_score": _mean(
            [
                float(row["preferred_tool_score"])
                for row in scored
                if row.get("preferred_tool_score") is not None
            ]
        ),
    }
    quality = {
        "eligible_cases": len(quality_rows),
        "precision_at_k": _mean(
            [float(row["precision_at_k"]) for row in quality_rows]
        ),
        "recall_at_k": _mean([float(row["recall_at_k"]) for row in quality_rows]),
        "ndcg": _mean([float(row["ndcg"]) for row in quality_rows if row.get("ndcg") is not None]),
        "map": _mean([float(row["average_precision"]) for row in quality_rows]),
        "mrr": _mean([float(row["reciprocal_rank"]) for row in quality_rows]),
        "spearman": _mean(
            [float(row["spearman"]) for row in quality_rows if row.get("spearman") is not None]
        ),
        "mae": _mean([float(row["mae"]) for row in quality_rows if row.get("mae") is not None]),
        "quality_score": _mean(
            [float(row["score"]) for row in quality_rows if row.get("score") is not None]
        ),
    }
    failures = [
        row["failure"] for row in scored if isinstance(row.get("failure"), dict)
    ]
    metrics = {
        "behavior": behavior,
        # Compatibility alias for existing baselines/report consumers.
        "task": {
            "total": n,
            "passed": behavior["passed"],
            "task_success_rate": behavior["success_rate"],
            "intent_accuracy": behavior["intent_accuracy"],
            "tool_accuracy": behavior["preferred_tool_score"],
            "precision_at_k": quality["precision_at_k"],
            "recall_at_k": quality["recall_at_k"],
        },
        "trajectory": {
            "average_steps": _mean([float(row["steps"]) for row in scored]),
            "average_tool_calls": _mean([float(row["tool_calls"]) for row in scored]),
            "reflection_count": sum(int(row["reflection_count"]) for row in scored),
            "retry_count": sum(int(row["retry_count"]) for row in scored),
            "loop_count": sum(1 for row in scored if row["loop_detected"]),
            "max_depth": max((int(row["max_depth"]) for row in scored), default=0),
            "trajectory_score": _mean(
                [
                    float(row["trajectory_score"])
                    for row in scored
                    if row.get("trajectory_score") is not None
                ]
            ),
        },
        "runtime": {
            "total_latency_ms": round(sum(latencies), 3),
            "p50_latency_ms": _percentile(latencies, 0.5),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "average_llm_calls": _mean([float(row["llm_calls"]) for row in scored]),
            "average_inference_calls": _mean(
                [float(row["inference_calls"]) for row in scored]
            ),
            "average_tokens": _mean(token_counts),
            "average_cost_usd": _mean(
                [
                    float(
                        (row.get("token_usage") or {}).get("estimated_cost_usd") or 0
                    )
                    for row in executions
                ]
            ),
            "average_queue_wait_ms": _mean(
                [
                    float((row.get("runtime") or {}).get("queue_wait_ms") or 0)
                    for row in executions
                ]
            ),
            "cost_score": _mean(
                [
                    float(row["cost_score"])
                    for row in scored
                    if row.get("cost_score") is not None
                ]
            ),
            "token_source": "estimated",
        },
        "quality": quality,
        "failure_counts": {
            category: sum(1 for failure in failures if failure["category"] == category)
            for category in sorted({failure["category"] for failure in failures})
        },
    }
    return metrics


def composite_score(
    metrics: dict[str, Any], weights: dict[str, float]
) -> dict[str, Any]:
    """Calculate a configurable 0–100 score, redistributing unavailable weight."""
    components: dict[str, float | None] = {
        "task_success": (
            float((metrics.get("behavior") or {}).get("success_rate") or 0) * 100
        ),
        "quality": (metrics.get("quality") or {}).get("quality_score"),
        "cost": (metrics.get("runtime") or {}).get("cost_score"),
        "trajectory": (metrics.get("trajectory") or {}).get("trajectory_score"),
    }
    configured = {
        "task_success": float(weights.get("task_success", 0.40)),
        "quality": float(weights.get("quality", 0.30)),
        "cost": float(weights.get("cost", 0.15)),
        "trajectory": float(weights.get("trajectory", 0.15)),
    }
    available_weight = sum(
        configured[name] for name, value in components.items() if value is not None
    )
    effective = {
        name: (
            round(configured[name] / available_weight, 4)
            if value is not None and available_weight > 0
            else 0.0
        )
        for name, value in components.items()
    }
    overall = sum(
        float(value) * effective[name]
        for name, value in components.items()
        if value is not None
    )
    return {
        "schema_version": "agent_evaluation_score.v1",
        "overall_score": round(overall, 2),
        **{
            name: round(float(value), 2) if value is not None else None
            for name, value in components.items()
        },
        "configured_weights": configured,
        "effective_weights": effective,
    }

