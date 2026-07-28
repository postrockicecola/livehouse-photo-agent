"""Heuristic signals that mark a Gallery conversation turn as worth human review.

Used by ``scripts/agent/export_review_queue.py``. Keep this module dependency-light
(no LLM): pure rules over messages + ``agent_events`` payloads.
"""
from __future__ import annotations

from typing import Any, Optional

from services.agent.intent_router import has_count_shortlist_phrase


def pair_user_turns(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Zip user messages with done/tool/guardrail events into per-turn dicts."""
    users = [m for m in messages if (m.get("role") or "") == "user"]
    turns_from_events: list[dict[str, Any]] = []
    current_tools: list[dict[str, Any]] = []
    current_guards: list[dict[str, Any]] = []
    for ev in events:
        et = str(ev.get("type") or "")
        if et == "tool_call":
            current_tools.append(ev)
        elif et == "guardrail":
            current_guards.append(ev)
        elif et == "done":
            tcs = list(ev.get("tool_calls") or current_tools)
            turns_from_events.append(
                {
                    "tool_calls": tcs,
                    "reply": ev.get("reply"),
                    "routed": ev.get("routed"),
                    "guardrails": list(current_guards),
                }
            )
            current_tools = []
            current_guards = []
    if current_tools or current_guards:
        turns_from_events.append(
            {
                "tool_calls": list(current_tools),
                "reply": None,
                "routed": None,
                "guardrails": list(current_guards),
            }
        )

    out: list[dict[str, Any]] = []
    for i, u in enumerate(users):
        base = (
            turns_from_events[i]
            if i < len(turns_from_events)
            else {"tool_calls": [], "reply": None, "routed": None, "guardrails": []}
        )
        out.append({"user_text": str(u.get("content") or ""), **base})
    return out


def turn_was_routed(turn: dict[str, Any]) -> bool:
    if turn.get("routed"):
        return True
    for tc in turn.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        if tc.get("routed"):
            return True
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        if meta.get("routed"):
            return True
    return False


def collect_turn_reasons(turn: dict[str, Any]) -> list[str]:
    """Return signal tags for one user turn (may be empty)."""
    reasons: list[str] = []
    guards = turn.get("guardrails") or []
    if any(isinstance(g, dict) and g.get("triggered") for g in guards):
        reasons.append("guardrail_triggered")
    # Also accept guardrail payloads flattened onto the turn via event merge.
    if turn.get("type") == "guardrail" and turn.get("triggered"):
        reasons.append("guardrail_triggered")

    for tc in turn.get("tool_calls") or []:
        if isinstance(tc, dict) and tc.get("ok") is False:
            reasons.append("tool_call_failed")
            break

    if has_count_shortlist_phrase(str(turn.get("user_text") or "")) and not turn_was_routed(turn):
        reasons.append("route_near_miss")

    # de-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def collect_conversation_reasons(
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Aggregate reasons across turns; return ``(reasons, flagged_turns)``."""
    turns = pair_user_turns(messages, events)
    # Conversation-level guardrail events not yet folded into a done turn.
    orphan_guards = [
        e for e in events if e.get("type") == "guardrail" and e.get("triggered")
    ]
    flagged: list[dict[str, Any]] = []
    reason_set: list[str] = []
    seen: set[str] = set()

    def _add(r: str) -> None:
        if r not in seen:
            seen.add(r)
            reason_set.append(r)

    if orphan_guards:
        _add("guardrail_triggered")

    for t in turns:
        rs = collect_turn_reasons(t)
        if not rs and orphan_guards and t is turns[-1]:
            # Attach orphans to the last user turn for review context.
            rs = ["guardrail_triggered"]
            t = {**t, "guardrails": list(t.get("guardrails") or []) + orphan_guards}
        if rs:
            for r in rs:
                _add(r)
            flagged.append({**t, "reasons": rs})
    return reason_set, flagged


def annotation_stub(
    *,
    conversation_id: int,
    reasons: list[str],
    user_text: str,
    turn: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Empty review schema for a human to fill in (stage ②)."""
    return {
        "conversation_id": conversation_id,
        "reasons": list(reasons),
        "user_text": user_text,
        "issue_type": "",  # missed_route | wrong_route | negation_missed | ...
        "expected_behavior": {
            "should_route": None,
            "rule_id": None,
            "expected_args": {},
        },
        "action": "",  # add_regression_test | needs_prompt_fix | not_a_bug | ignore
        "notes": "",
        "reviewed_by": "",
        "reviewed_at": "",
        "turn": {
            "tool_calls": (turn or {}).get("tool_calls") or [],
            "reply": (turn or {}).get("reply"),
            "routed": (turn or {}).get("routed"),
            "guardrails": (turn or {}).get("guardrails") or [],
        },
    }
