"""Turn-level trace fields on ConversationalAgent done events."""
from __future__ import annotations

import json

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.skills.base import SkillRegistry, SkillResult


def test_routed_turn_trace_has_rule_and_rounds(tmp_path) -> None:
    rows = [
        {
            "file": f"p{i}.jpg",
            "overall_score": 90.0 - i,
            "scores": {"overall": 90.0 - i, "energy": 8.0, "technical": 8.0, "composition": 8.0},
            "energy": 8.0,
            "technical": 8.0,
            "composition": 8.0,
            "category": "AI_Best_90+",
            "tags": ["stage"],
            "reason": "ok",
        }
        for i in range(12)
    ]
    (tmp_path / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")
    from services.agent.skills.gallery import gallery_registry

    agent = ConversationalAgent(
        lambda _m: "已选出短名单。",
        memory=ConversationMemory(system_prompt="test"),
        skills=gallery_registry(str(tmp_path)),
        wrap_tool_output=False,
    )
    res = agent.chat("选出10张")
    assert res.trace.get("backend", "").startswith("routed:")
    assert res.trace.get("rule_id") == "shortlist_select"
    assert res.trace.get("rounds_used", 0) >= 1
    assert res.trace.get("grounding_ok") is True
    done = [e for e in res.events if e.get("type") == "done"]
    assert done and done[-1].get("trace", {}).get("rule_id") == "shortlist_select"


def test_grounding_violation_sets_trace_flag() -> None:
    class _Search:
        name = "gallery_search"
        description = "s"
        parameters = {"type": "object", "properties": {}}

        def run(self, args):
            return SkillResult(ok=True, output="1", metadata={"files": ["real.jpg"]})

    reg = SkillRegistry()
    reg.register(_Search())
    # LangGraph: search returns cite files → lean final (decide prose discarded).
    scripted = iter(
        [
            json.dumps({"tool": "gallery_search", "args": {"query": "x"}}),
            "ignored decide prose",
            "推荐 ghost.jpg",
        ]
    )
    agent = ConversationalAgent(lambda _m: next(scripted), skills=reg, wrap_tool_output=False)
    res = agent.chat("找一下")
    assert res.trace.get("grounding_ok") is False
    assert any(e.get("type") == "grounding_violation" for e in res.events)
