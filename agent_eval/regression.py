"""Categorized current-versus-baseline regression gates."""
from __future__ import annotations

from typing import Any

DEFAULT_THRESHOLDS: dict[str, float] = {
    "min_behavior_success_rate": 0.80,
    "min_intent_accuracy": 0.90,
    "min_overall_score": 80.0,
    "max_loop_count": 0,
    "max_success_drop": 0.05,
    "max_quality_drop": 0.05,
    "max_steps_increase_pct": 0.20,
    "max_tool_calls_increase_pct": 0.20,
    "max_token_increase_pct": 0.25,
    "max_inference_increase_pct": 0.25,
    "max_latency_increase_pct": 0.25,
}


def _value(metrics: dict[str, Any], section: str, key: str) -> float | None:
    value = (metrics.get(section) or {}).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _behavior_success(metrics: dict[str, Any]) -> float:
    value = _value(metrics, "behavior", "success_rate")
    if value is None:
        value = _value(metrics, "task", "task_success_rate")
    return float(value or 0)


def _quality(metrics: dict[str, Any]) -> float | None:
    section = metrics.get("quality") or {}
    for key in ("quality_score", "ndcg", "map", "precision_at_k"):
        value = section.get(key)
        if isinstance(value, (int, float)):
            numeric = float(value)
            return numeric / 100.0 if key == "quality_score" else numeric
    return None


def threshold_failures(
    metrics: dict[str, Any],
    thresholds: dict[str, float],
    score: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    intent = _value(metrics, "behavior", "intent_accuracy")
    checks: list[tuple[str, float, str, float]] = [
        (
            "behavior.success_rate",
            _behavior_success(metrics),
            ">=",
            thresholds["min_behavior_success_rate"],
        ),
        (
            "trajectory.loop_count",
            float(_value(metrics, "trajectory", "loop_count") or 0),
            "<=",
            thresholds["max_loop_count"],
        ),
    ]
    if intent is not None:
        checks.append(
            ("behavior.intent_accuracy", intent, ">=", thresholds["min_intent_accuracy"])
        )
    if score and isinstance(score.get("overall_score"), (int, float)):
        checks.append(
            (
                "score.overall_score",
                float(score["overall_score"]),
                ">=",
                thresholds["min_overall_score"],
            )
        )
    failures: list[dict[str, Any]] = []
    for metric, current, operator, target in checks:
        failed = current < target if operator == ">=" else current > target
        if failed:
            failures.append(
                {
                    "metric": metric,
                    "current": current,
                    "operator": operator,
                    "threshold": target,
                }
            )
    return failures


def _increase(current: float, baseline: float) -> float:
    if baseline <= 0:
        return 0.0 if current <= 0 else 1_000_000.0
    return (current - baseline) / baseline


def _regression(
    category: str,
    metric: str,
    current: float,
    baseline: float,
    observed: float,
    allowed: float,
    explanation: str,
) -> dict[str, Any]:
    return {
        "category": category,
        "metric": metric,
        "current": round(current, 4),
        "baseline": round(baseline, 4),
        "observed_change": round(observed, 4),
        "allowed_change": allowed,
        "explanation": f"System became worse because {explanation}.",
    }


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any] | None,
    thresholds: dict[str, float],
) -> dict[str, Any]:
    baseline_metrics = (baseline or {}).get("metrics")
    if not isinstance(baseline_metrics, dict):
        return {
            "available": False,
            "passed": True,
            "regressions": [],
            "categories": {},
            "deltas": {},
            "summary": "No measured baseline is available.",
        }

    regressions: list[dict[str, Any]] = []
    deltas: dict[str, float | None] = {}

    current_success = _behavior_success(current)
    baseline_success = _behavior_success(baseline_metrics)
    success_drop = baseline_success - current_success
    deltas["behavior_success"] = round(current_success - baseline_success, 4)
    if success_drop > thresholds["max_success_drop"]:
        regressions.append(
            _regression(
                "Behavior Regression",
                "behavior.success_rate",
                current_success,
                baseline_success,
                success_drop,
                thresholds["max_success_drop"],
                "fewer behavioral contracts completed successfully",
            )
        )

    current_quality = _quality(current)
    baseline_quality = _quality(baseline_metrics)
    deltas["quality"] = (
        round(current_quality - baseline_quality, 4)
        if current_quality is not None and baseline_quality is not None
        else None
    )
    if (
        current_quality is not None
        and baseline_quality is not None
        and baseline_quality - current_quality > thresholds["max_quality_drop"]
    ):
        regressions.append(
            _regression(
                "Quality Regression",
                "quality.ranking",
                current_quality,
                baseline_quality,
                baseline_quality - current_quality,
                thresholds["max_quality_drop"],
                "the selected-photo ranking moved farther from the golden dataset",
            )
        )

    efficiency_specs = (
        ("average_steps", "max_steps_increase_pct", "the agent used more planning/action steps"),
        (
            "average_tool_calls",
            "max_tool_calls_increase_pct",
            "the agent invoked more tools to complete the same contracts",
        ),
    )
    for metric, threshold_key, explanation in efficiency_specs:
        cur = float(_value(current, "trajectory", metric) or 0)
        base = float(_value(baseline_metrics, "trajectory", metric) or 0)
        change = _increase(cur, base)
        deltas[metric] = round(change, 4)
        if change > thresholds[threshold_key]:
            regressions.append(
                _regression(
                    "Efficiency Regression",
                    f"trajectory.{metric}",
                    cur,
                    base,
                    change,
                    thresholds[threshold_key],
                    explanation,
                )
            )

    cost_specs = (
        ("average_tokens", "max_token_increase_pct", "average token usage increased"),
        (
            "average_inference_calls",
            "max_inference_increase_pct",
            "expensive inference calls increased",
        ),
    )
    for metric, threshold_key, explanation in cost_specs:
        cur = float(_value(current, "runtime", metric) or 0)
        base = float(_value(baseline_metrics, "runtime", metric) or 0)
        change = _increase(cur, base)
        deltas[metric] = round(change, 4)
        if change > thresholds[threshold_key]:
            regressions.append(
                _regression(
                    "Cost Regression",
                    f"runtime.{metric}",
                    cur,
                    base,
                    change,
                    thresholds[threshold_key],
                    explanation,
                )
            )

    cur_latency = float(_value(current, "runtime", "p95_latency_ms") or 0)
    base_latency = float(_value(baseline_metrics, "runtime", "p95_latency_ms") or 0)
    latency_change = _increase(cur_latency, base_latency)
    deltas["p95_latency_ms"] = round(latency_change, 4)
    if latency_change > thresholds["max_latency_increase_pct"]:
        regressions.append(
            _regression(
                "Runtime Regression",
                "runtime.p95_latency_ms",
                cur_latency,
                base_latency,
                latency_change,
                thresholds["max_latency_increase_pct"],
                "P95 end-to-end latency increased",
            )
        )

    categories = {
        category: sum(1 for row in regressions if row["category"] == category)
        for category in sorted({row["category"] for row in regressions})
    }
    summary = (
        "No blocking regression detected."
        if not regressions
        else " ".join(row["explanation"] for row in regressions)
    )
    return {
        "available": True,
        "passed": not regressions,
        "regressions": regressions,
        "categories": categories,
        "deltas": deltas,
        "summary": summary,
    }

