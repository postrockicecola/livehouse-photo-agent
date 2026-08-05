"""Build safe, observable decision traces without chain-of-thought."""
from __future__ import annotations

from typing import Any

_ACTION_PURPOSES = {
    "gallery_search": "Retrieve candidates that satisfy the user request.",
    "gallery_select": "Persist candidates as the current Gallery selection.",
    "gallery_stats": "Read aggregate statistics for the current photo session.",
    "gallery_export": "Export the already selected photo set.",
    "remember_preference": "Persist an explicit user preference for later turns.",
}


def _result_summary(span: dict[str, Any]) -> dict[str, Any]:
    output = span.get("output")
    if not isinstance(output, dict):
        return {"ok": bool(span.get("ok")), "summary": str(output or "")[:300]}
    metadata = output.get("metadata") if isinstance(output.get("metadata"), dict) else {}
    files = metadata.get("selected_keys") or metadata.get("files") or []
    return {
        "ok": bool(span.get("ok")),
        "summary": str(output.get("output") or output.get("error") or "")[:300],
        "result_count": metadata.get("count"),
        "files_count": len(files) if isinstance(files, list) else None,
        "state_change": metadata.get("ui_action"),
        "latency_ms": span.get("latency_ms"),
    }


def build_decision_trace(
    *,
    user_input: str,
    analyzed_photo_count: int | None,
    planner: list[dict[str, Any]],
    tool_spans: list[dict[str, Any]],
    reply: str,
) -> list[dict[str, Any]]:
    """Convert observable planner/actions/results into debugger-friendly steps."""
    model_decisions = [
        item for item in planner if item.get("type") == "tool_decision"
    ]
    routed = next(
        (item for item in planner if item.get("type") == "deterministic_route"),
        None,
    )
    steps: list[dict[str, Any]] = []
    previous_result: dict[str, Any] | None = None
    for index, span in enumerate(tool_spans, start=1):
        tool = str(span.get("tool") or "unknown")
        matching = next(
            (item for item in model_decisions if item.get("tool") == tool),
            None,
        )
        decision_source = (
            "model_tool_decision"
            if matching is not None
            else "deterministic_route"
            if routed is not None
            else "runtime_dispatch"
        )
        result = _result_summary(span)
        steps.append(
            {
                "step": index,
                "observation": {
                    "user_request": user_input,
                    "analyzed_photos_available": analyzed_photo_count,
                    "prior_result": previous_result,
                },
                "decision": {
                    "summary": _ACTION_PURPOSES.get(
                        tool, f"Invoke {tool} to advance the requested workflow."
                    ),
                    "source": decision_source,
                    "rule_id": routed.get("rule_id") if routed else None,
                },
                "action": {
                    "tool": tool,
                    "parameters": dict(span.get("parameters") or {}),
                },
                "result": result,
            }
        )
        previous_result = result
    steps.append(
        {
            "step": len(steps) + 1,
            "observation": {
                "user_request": user_input,
                "prior_result": previous_result,
            },
            "decision": {
                "summary": "Return the final user-facing answer from observable results.",
                "source": "answer_node",
            },
            "action": {"type": "respond"},
            "result": {"reply": str(reply or "")[:1000]},
        }
    )
    return steps

