"""Tool JSON repair pass (Phase 5)."""
from __future__ import annotations

import json

from services.agent.conversation import ConversationalAgent
from services.agent.skills.base import SkillRegistry, SkillResult
from services.agent.tool_protocol import (
    looks_like_tool_intent,
    parse_tool_call,
    parse_tool_call_with_repair,
)


def test_looks_like_tool_intent() -> None:
    assert looks_like_tool_intent('{"tool": "x", "args":')
    assert looks_like_tool_intent("```json\n{broken")
    assert not looks_like_tool_intent("本场没有萨克斯。")


def test_parse_tool_call_with_repair_succeeds() -> None:
    calls: list[list[dict]] = []

    def chat_fn(messages):
        calls.append(list(messages))
        return json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}})

    call, raw, repaired = parse_tool_call_with_repair(
        chat_fn,
        [{"role": "user", "content": "找鼓手"}],
        '{"tool": "gallery_search", "args": {',  # broken
    )
    assert repaired
    assert call == {"tool": "gallery_search", "args": {"query": "鼓手"}}
    assert parse_tool_call(raw) == call
    assert len(calls) == 1


def test_agent_repairs_broken_tool_json_then_acts() -> None:
    class _Search:
        name = "gallery_search"
        description = "s"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(ok=True, output="ok", metadata={"files": ["a.jpg"]})

    reg = SkillRegistry()
    reg.register(_Search())
    n = {"i": 0}

    def chat_fn(messages):
        n["i"] += 1
        if n["i"] == 1:
            return '{"tool": "gallery_search", "args": {'  # broken first decide
        if any(m.get("role") == "user" and "valid tool JSON" in (m.get("content") or "") for m in messages):
            return json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}})
        return "推荐 a.jpg"

    agent = ConversationalAgent(chat_fn, skills=reg, wrap_tool_output=False, max_tool_rounds=2)
    res = agent.chat("找鼓手")
    assert any(tc.get("tool") == "gallery_search" for tc in res.tool_calls)
    assert res.trace.get("parse_repaired") is True
    assert any(e.get("type") == "parse_repair" and e.get("ok") for e in res.events)
