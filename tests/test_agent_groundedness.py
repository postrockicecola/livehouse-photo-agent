"""Unit tests for final-answer file groundedness + {ref_N} placeholders."""
from __future__ import annotations

import json

import pytest

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.conversation_graph import langgraph_available
from services.agent.groundedness import (
    build_grounding_event,
    check_groundedness,
    collect_allowed_files,
    extract_file_mentions,
    ground_reply,
    grounding_failure_template,
    ordered_ref_files,
    resolve_ref_placeholders,
    rewrite_ungrounded_reply,
)
from services.agent.skills.base import SkillRegistry, SkillResult


@pytest.fixture(autouse=True)
def _chat_runtime_without_langgraph(monkeypatch):
    """Offline dual-path helpers only — not LangGraph production verification.

    Production cite-path coverage lives in ``test_agent_conversation_graph``
    (``@pytest.mark.requires_langgraph``).
    """
    if not langgraph_available():
        monkeypatch.setenv("LIVEHOUSE_AGENT_RUNTIME", "imperative")


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


def test_resolve_ref_placeholders_happy_path() -> None:
    files = ["drum_01.jpg", "guitar_02.jpg"]
    text, v = resolve_ref_placeholders(
        "这几张照片里 {ref_0} 构图最好，{ref_1} 是抓拍瞬间。",
        files,
    )
    assert v.ok
    assert v.unresolved == []
    assert text == "这几张照片里 drum_01.jpg 构图最好，guitar_02.jpg 是抓拍瞬间。"


def test_resolve_ref_placeholders_oob_no_crash() -> None:
    files = ["a.jpg", "b.jpg", "c.jpg"]
    raw = "看看 {ref_0} 和 {ref_5}"
    text, v = resolve_ref_placeholders(raw, files)
    assert not v.ok
    assert 5 in v.unresolved
    assert text == raw  # unchanged on failure — caller applies template


def test_ground_reply_strips_unknown_keeps_grounded_prose() -> None:
    text, v = ground_reply(
        "推荐 fake_hallucinated_99.jpg 以及 drum_01.jpg，节奏不错。",
        tool_calls=[{"metadata": {"files": ["drum_01.jpg"]}}],
    )
    assert v.triggered
    assert v.rewrite_mode == "strip"
    assert "fake_hallucinated_99.jpg" not in text
    assert "节奏不错" in text
    assert "drum_01.jpg" in text


def test_ground_reply_empty_tool_result_blocks_invented_files() -> None:
    text, v = ground_reply(
        "推荐 sax_01.jpg",
        tool_calls=[{"tool": "gallery_search", "metadata": {"files": []}}],
    )
    assert v.triggered
    assert "sax_01.jpg" not in text
    assert "没有返回" in text or "不能列举" in text


def test_ground_reply_skips_without_signal() -> None:
    text, v = ground_reply("我可以帮你搜索和初选。", tool_calls=[], working_memory={})
    assert v.ok
    assert text == "我可以帮你搜索和初选。"


def test_rewrite_mixed_cites_strips_unknown() -> None:
    out, mode = rewrite_ungrounded_reply(
        "推荐 x.jpg 和 a.jpg，构图干净。",
        {"b.jpg", "a.jpg"},
        ["x.jpg"],
    )
    assert mode == "strip"
    assert "x.jpg" not in out
    assert "a.jpg" in out
    assert "构图干净" in out


def test_rewrite_all_unknown_uses_template() -> None:
    out, mode = rewrite_ungrounded_reply("看 x.jpg", {"b.jpg", "a.jpg"}, ["x.jpg"])
    assert mode == "template"
    assert "x.jpg" not in out
    assert "根据搜索结果" in out


def test_build_grounding_event_guardrail_shape() -> None:
    ev = build_grounding_event(
        reason="invented_file",
        user_text="找鼓手",
        raw_model_output="推荐 ghost.jpg",
        final_reply=grounding_failure_template(["drum_01.jpg"]),
    )
    assert ev["type"] == "grounding_violation"
    assert ev["kind"] == "groundedness"
    assert ev["triggered"] is True
    assert "invented_file" in ev["matches"]
    detail = ev["detail"]
    assert detail["user_text"] == "找鼓手"
    assert detail["raw_model_output"] == "推荐 ghost.jpg"
    assert "drum_01.jpg" in detail["final_reply"]
    assert "ts" in detail


def test_agent_finalize_resolves_refs() -> None:
    class _Search:
        name = "gallery_search"
        description = "search"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(
                ok=True,
                output="2 hits",
                metadata={"files": ["drum_01.jpg", "guitar_02.jpg"], "count": 2},
            )

    reg = SkillRegistry()
    reg.register(_Search())
    scripted = iter(
        [
            json.dumps({"tool": "gallery_search", "args": {"query": "吉他手"}}),
            "这几张照片里 {ref_0} 构图最好，{ref_1} 是抓拍瞬间。",
        ]
    )
    agent = ConversationalAgent(
        lambda _m: next(scripted),
        memory=ConversationMemory(system_prompt="test"),
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=1,  # tool then lean final-answer (force path)
    )
    res = agent.chat("找吉他手")
    assert "drum_01.jpg" in res.reply
    assert "guitar_02.jpg" in res.reply
    assert "{ref_" not in res.reply
    assert not any(e.get("type") == "grounding_violation" for e in res.events)


def test_agent_finalize_oob_ref_uses_template_and_records() -> None:
    class _Search:
        name = "gallery_search"
        description = "search"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(
                ok=True,
                output="1 hit",
                metadata={"files": ["a.jpg", "b.jpg", "c.jpg"], "count": 3},
            )

    reg = SkillRegistry()
    reg.register(_Search())
    scripted = iter(
        [
            json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}}),
            "推荐 {ref_0} 和 {ref_5}。",
        ]
    )
    agent = ConversationalAgent(
        lambda _m: next(scripted),
        memory=ConversationMemory(system_prompt="test"),
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=1,
    )
    res = agent.chat("找鼓手")
    assert res.reply == grounding_failure_template(["a.jpg", "b.jpg", "c.jpg"])
    hits = [e for e in res.events if e.get("type") == "grounding_violation"]
    assert hits
    ev = hits[0]
    assert ev.get("triggered") is True
    assert ev.get("kind") == "groundedness"
    assert "oob_ref" in (ev.get("matches") or [])
    assert ev.get("detail", {}).get("user_text") == "找鼓手"
    assert "{ref_5}" in (ev.get("detail", {}).get("raw_model_output") or "")


def test_agent_finalize_invented_file_template_and_event() -> None:
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
            "推荐 fake_hallucinated_99.jpg 以及 drum_01.jpg，节奏不错。",
        ]
    )
    agent = ConversationalAgent(
        lambda _m: next(scripted),
        memory=ConversationMemory(system_prompt="test"),
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=1,
    )
    res = agent.chat("找鼓手")
    assert "fake_hallucinated_99.jpg" not in res.reply
    assert "drum_01.jpg" in res.reply
    assert "节奏不错" in res.reply
    hits = [e for e in res.events if e.get("type") == "grounding_violation"]
    assert hits
    ev = hits[0]
    assert ev["triggered"] is True
    assert ev["kind"] == "groundedness"
    assert "invented_file" in ev["matches"]
    assert ev["detail"]["user_text"] == "找鼓手"
    assert "fake_hallucinated_99.jpg" in ev["detail"]["raw_model_output"]
    assert ev["detail"]["final_reply"] == res.reply


def test_ordered_ref_files_stable() -> None:
    files = ordered_ref_files(
        [{"metadata": {"files": ["x/A.JPG", "b.jpg"]}}],
        working_memory={"last_files": ["b.jpg", "c.jpg"]},
    )
    assert files == ["A.JPG", "b.jpg", "c.jpg"]


def test_final_answer_messages_include_ref_index() -> None:
    class _Search:
        name = "gallery_search"
        description = "search"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(
                ok=True,
                output="ok",
                metadata={"files": ["drum_01.jpg"], "count": 1},
            )

    reg = SkillRegistry()
    reg.register(_Search())
    # Capture final-answer messages via side channel.
    captured: list[list[dict[str, str]]] = []

    def _chat(messages):
        # First call: tool JSON; subsequent: final answer (messages already lean).
        if any(
            isinstance(m.get("content"), str) and "PHOTO REFS" in (m.get("content") or "")
            for m in messages
        ):
            captured.append(list(messages))
            return "构图最好的是 {ref_0}。"
        return json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}})

    agent = ConversationalAgent(
        _chat,
        memory=ConversationMemory(system_prompt="test"),
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=1,
    )
    res = agent.chat("找鼓手")
    assert captured, "expected lean final-answer prompt with PHOTO REFS"
    user_blob = "\n".join(
        m.get("content") or "" for m in captured[0] if m.get("role") == "user"
    )
    assert "PHOTO REFS —" in user_blob
    tool_section, _, _rest = user_blob.partition("PHOTO REFS —")
    assert "{ref_0}" in tool_section  # filename redacted in tool results
    assert "drum_01.jpg" not in tool_section
    assert "drum_01.jpg" in res.reply
