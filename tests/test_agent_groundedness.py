"""Unit tests for final-answer file groundedness."""
from __future__ import annotations

import json

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.groundedness import (
    check_groundedness,
    collect_allowed_files,
    extract_file_mentions,
    ground_reply,
    rewrite_ungrounded_reply,
)
from services.agent.skills.base import SkillRegistry, SkillResult


def test_extract_file_mentions_unique_order() -> None:
    text = "推荐 drum_01.jpg 和 guitar_01.JPG，再看 drum_01.jpg。"
    assert extract_file_mentions(text) == ["drum_01.jpg", "guitar_01.jpg"]


def test_check_groundedness_flags_unknown() -> None:
    v = check_groundedness(
        "推荐 fake_99.jpg 和 drum_01.jpg",
        {"drum_01.jpg"},
    )
    assert not v.ok
    assert v.unknown == ["fake_99.jpg"]
    assert "drum_01.jpg" in v.cited


def test_collect_allowed_from_tools_and_wm() -> None:
    allowed = collect_allowed_files(
        [{"metadata": {"files": ["a/Drum_01.JPG"]}, "args": {}}],
        working_memory={"last_files": ["guitar_01.jpg"]},
    )
    assert allowed == {"drum_01.jpg", "guitar_01.jpg"}


def test_ground_reply_rewrites_when_ungrounded() -> None:
    text, v = ground_reply(
        "请看 ghost.jpg",
        tool_calls=[{"metadata": {"files": ["drum_01.jpg"]}}],
    )
    assert v.triggered
    assert "ghost.jpg" not in text
    assert "drum_01.jpg" in text


def test_ground_reply_skips_without_signal() -> None:
    text, v = ground_reply("我可以帮你搜索和初选。", tool_calls=[], working_memory={})
    assert v.ok
    assert text == "我可以帮你搜索和初选。"


def test_empty_tool_result_blocks_invented_files() -> None:
    text, v = ground_reply(
        "推荐 sax_01.jpg",
        tool_calls=[{"tool": "gallery_search", "metadata": {"files": []}}],
    )
    assert v.triggered
    assert "sax_01.jpg" not in text
    assert "没有返回" in text or "不能列举" in text


def test_rewrite_lists_allowed() -> None:
    out = rewrite_ungrounded_reply({"b.jpg", "a.jpg"}, ["x.jpg"])
    assert "a.jpg" in out and "b.jpg" in out
    assert "x.jpg" not in out


def test_agent_finalize_rewrites_hallucinated_file() -> None:
    class _Search:
        name = "gallery_search"
        description = "search"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(
                ok=True,
                output="1 hit",
                metadata={"files": ["drum_01.jpg"], "count": 1},
            )

    reg = SkillRegistry()
    reg.register(_Search())
    scripted = iter(
        [
            json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}}),
            "推荐 fake_hallucinated_99.jpg 以及 drum_01.jpg。",
        ]
    )
    agent = ConversationalAgent(
        lambda _m: next(scripted),
        memory=ConversationMemory(system_prompt="test"),
        skills=reg,
        wrap_tool_output=False,
    )
    res = agent.chat("找鼓手")
    assert "fake_hallucinated_99.jpg" not in res.reply
    assert any(e.get("type") == "grounding_violation" for e in res.events)
    assert "drum_01.jpg" in res.reply
