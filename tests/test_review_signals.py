"""Unit tests for review-queue signal heuristics (no DB / no LLM)."""
from __future__ import annotations

from services.agent.review_signals import (
    collect_conversation_reasons,
    collect_turn_reasons,
    pair_user_turns,
    turn_was_routed,
)


def test_route_near_miss_when_count_phrase_unrouted() -> None:
    turn = {
        "user_text": "帮我选20张照片",
        "tool_calls": [{"tool": "gallery_search", "args": {"query": "帮我选20张照片"}, "ok": True, "metadata": {}}],
        "routed": None,
        "guardrails": [],
    }
    assert "route_near_miss" in collect_turn_reasons(turn)


def test_no_near_miss_when_routed() -> None:
    turn = {
        "user_text": "帮我选20张照片",
        "tool_calls": [
            {
                "tool": "gallery_search",
                "args": {"limit": 20},
                "ok": True,
                "metadata": {"routed": "shortlist_select"},
            }
        ],
        "routed": "shortlist_select",
        "guardrails": [],
    }
    assert turn_was_routed(turn)
    assert "route_near_miss" not in collect_turn_reasons(turn)


def test_guardrail_triggered() -> None:
    turn = {
        "user_text": "hi",
        "tool_calls": [],
        "guardrails": [{"type": "guardrail", "triggered": True, "kind": "prompt_injection"}],
    }
    assert "guardrail_triggered" in collect_turn_reasons(turn)


def test_tool_call_failed() -> None:
    turn = {
        "user_text": "导出",
        "tool_calls": [{"tool": "export_selected", "ok": False, "metadata": {}}],
        "guardrails": [],
    }
    assert "tool_call_failed" in collect_turn_reasons(turn)


def test_pair_and_collect_conversation() -> None:
    messages = [
        {"role": "user", "content": "帮我选20张照片"},
        {"role": "assistant", "content": "好的"},
    ]
    events = [
        {
            "type": "tool_call",
            "tool": "gallery_search",
            "args": {},
            "ok": True,
            "metadata": {},
        },
        {"type": "done", "reply": "好的", "tool_calls": [{"tool": "gallery_search", "ok": True, "metadata": {}}]},
    ]
    reasons, flagged = collect_conversation_reasons(messages, events)
    assert "route_near_miss" in reasons
    assert flagged and flagged[0]["user_text"] == "帮我选20张照片"
    turns = pair_user_turns(messages, events)
    assert len(turns) == 1
