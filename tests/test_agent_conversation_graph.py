"""Gallery chat LangGraph subgraph — production-path tests.

Branch coverage
---------------
Must stay in sync with ``ANSWER_BRANCH_CHECKLIST`` in
``services/agent/conversation_graph.py``. Each checklist ``id`` has a
``TestAnswerBranch<Id>`` class below, marked ``requires_langgraph``.

If langgraph is missing, those classes **fail** (see ``tests/conftest.py``),
they do not skip and they do not silently run on ``imperative``.
"""
from __future__ import annotations

import json

import pytest

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.conversation_graph import (
    ANSWER_BRANCH_CHECKLIST,
    GALLERY_CHAT_MAPPING,
    select_answer_branch,
    tool_calls_have_cite_files,
)
from services.agent.groundedness import grounding_failure_template
from services.agent.skills.base import SkillRegistry, SkillResult
from tests.conftest import LANGGRAPH_REQUIRED_FAIL_MSG, enforce_langgraph_available

# Checklist id → owning test class name (review: counts must match).
_BRANCH_TEST_CLASSES = {
    "lean_refs": "TestAnswerBranchLeanRefs",
    "direct_no_files": "TestAnswerBranchDirectNoFiles",
    "plain_no_tools": "TestAnswerBranchPlainNoTools",
}


def _echo_skill():
    class _Echo:
        name = "echo"
        description = "echo"
        parameters = {"type": "object", "properties": {"v": {"type": "string"}}}

        def run(self, args):
            return SkillResult(ok=True, output=str(args.get("v", "")))

    return _Echo()


def _search_skill(files: list[str]):
    class _Search:
        name = "gallery_search"
        description = "search"
        parameters = {"type": "object", "properties": {"query": {"type": "string"}}}

        def run(self, args):
            return SkillResult(
                ok=True,
                output=f"{len(files)} hits",
                metadata={"files": list(files), "count": len(files)},
            )

    return _Search()


def _scripted_chat(responses: list[str]):
    """Pop scripted model outputs; last response repeats (avoids StopIteration flakes)."""
    queue = list(responses)

    def _fn(_msgs):
        if not queue:
            return responses[-1] if responses else "ok"
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)

    return _fn


# ---------------------------------------------------------------------------
# Meta / pure helpers (no LangGraph runtime required)
# ---------------------------------------------------------------------------


def test_gallery_chat_mapping_mentions_subgraph():
    assert "ConversationalAgent.chat" in GALLERY_CHAT_MAPPING
    assert "node: decide" in GALLERY_CHAT_MAPPING.values()


def test_tool_calls_have_cite_files():
    assert tool_calls_have_cite_files([{"metadata": {"files": ["a.jpg"]}}])
    assert tool_calls_have_cite_files([{"metadata": {"selected_keys": ["b.jpg"]}}])
    assert not tool_calls_have_cite_files([{"metadata": {"files": []}}])
    assert not tool_calls_have_cite_files([{"tool": "echo", "metadata": {}}])


def test_answer_branch_checklist_matches_test_classes():
    """Fail the suite if a checklist branch has no owning TestAnswerBranch* class."""
    ids = [b["id"] for b in ANSWER_BRANCH_CHECKLIST]
    assert len(ids) == len(set(ids)), "duplicate ANSWER_BRANCH_CHECKLIST id"
    assert set(ids) == set(_BRANCH_TEST_CLASSES), (
        f"ANSWER_BRANCH_CHECKLIST ids {sorted(ids)} must match "
        f"_BRANCH_TEST_CLASSES keys {sorted(_BRANCH_TEST_CLASSES)}"
    )
    for bid, cls_name in _BRANCH_TEST_CLASSES.items():
        assert cls_name in globals(), f"missing test class {cls_name} for branch {bid}"
        cls = globals()[cls_name]
        assert getattr(cls, "checklist_id", None) == bid
        marks = getattr(cls, "pytestmark", [])
        if not isinstance(marks, list):
            marks = [marks]
        assert any(getattr(m, "name", None) == "requires_langgraph" for m in marks), (
            f"{cls_name} must be marked requires_langgraph"
        )


def test_select_answer_branch_covers_checklist_ids():
    """Pure picker — commenting out a branch in select_answer_branch fails here."""
    cases = {
        "lean_refs": select_answer_branch(
            has_direct=True,
            force=False,
            tool_calls=[{"metadata": {"files": ["a.jpg"]}}],
        ),
        "direct_no_files": select_answer_branch(
            has_direct=True,
            force=False,
            tool_calls=[{"metadata": {}}],
        ),
        "plain_no_tools": select_answer_branch(
            has_direct=False,
            force=False,
            tool_calls=[],
        ),
    }
    assert set(cases) == {b["id"] for b in ANSWER_BRANCH_CHECKLIST}
    for bid, got in cases.items():
        assert got == bid, f"select_answer_branch for {bid} returned {got}"


def test_requires_langgraph_fail_message_is_explicit():
    assert "production" in LANGGRAPH_REQUIRED_FAIL_MSG.lower()
    assert "langgraph" in LANGGRAPH_REQUIRED_FAIL_MSG.lower()
    assert "imperative" in LANGGRAPH_REQUIRED_FAIL_MSG.lower()


def test_enforce_langgraph_available_fails_when_missing(monkeypatch):
    """Missing langgraph → fail (not skip). Proves the guard is not a no-op."""
    monkeypatch.setattr(
        "services.agent.conversation_graph.langgraph_available",
        lambda: False,
    )
    with pytest.raises(pytest.fail.Exception, match="requires_langgraph"):
        enforce_langgraph_available()


# ---------------------------------------------------------------------------
# ANSWER_BRANCH_CHECKLIST integration (production LangGraph only)
# ---------------------------------------------------------------------------


@pytest.mark.requires_langgraph
class TestAnswerBranchLeanRefs:
    """Checklist id: lean_refs — file-bearing tools → lean PHOTO REFS → finalize."""

    checklist_id = "lean_refs"

    def test_file_search_uses_lean_refs_not_decide_prose(self):
        reg = SkillRegistry()
        reg.register(_search_skill(["drum_01.jpg", "guitar_02.jpg"]))
        lean_prompts: list[str] = []
        scripted = iter(
            [
                json.dumps({"tool": "gallery_search", "args": {"query": "吉他手"}}),
                "推荐 fake_hallucinated_99.jpg 和 drum_01.jpg。",
                "构图最好的是 {ref_0}，抓拍是 {ref_1}。",
            ]
        )

        def _scripted(msgs):
            blob = "\n".join(str(m.get("content") or "") for m in msgs)
            if "PHOTO REFS —" in blob:
                lean_prompts.append(blob)
            return next(scripted)

        agent = ConversationalAgent(
            _scripted,
            memory=ConversationMemory(system_prompt="test"),
            skills=reg,
            wrap_tool_output=False,
        )
        res = agent.chat("找吉他手")
        assert agent.last_backend == "langgraph"
        assert lean_prompts, "expected lean PHOTO REFS final-answer prompt"
        assert "fake_hallucinated_99.jpg" not in res.reply
        assert "drum_01.jpg" in res.reply
        assert "guitar_02.jpg" in res.reply
        assert "{ref_" not in res.reply

    def test_invented_file_in_lean_uses_template(self):
        reg = SkillRegistry()
        reg.register(_search_skill(["drum_01.jpg"]))
        scripted = iter(
            [
                json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}}),
                "ignored decide prose",
                "推荐 fake_hallucinated_99.jpg 以及 drum_01.jpg，节奏不错。",
            ]
        )
        agent = ConversationalAgent(
            lambda _m: next(scripted),
            memory=ConversationMemory(system_prompt="test"),
            skills=reg,
            wrap_tool_output=False,
        )
        res = agent.chat("找鼓手")
        assert agent.last_backend == "langgraph"
        assert res.reply == grounding_failure_template(["drum_01.jpg"])
        assert any(
            e.get("type") == "grounding_violation" and e.get("triggered")
            for e in res.events
        )

    def test_stream_file_search_uses_lean_messages(self):
        reg = SkillRegistry()
        reg.register(_search_skill(["drum_01.jpg"]))
        scripted = iter(
            [
                json.dumps({"tool": "gallery_search", "args": {"query": "鼓手"}}),
                "ignored decide prose with ghost.jpg",
                "值得保留的是 {ref_0}。",
            ]
        )
        agent = ConversationalAgent(
            lambda _m: next(scripted),
            memory=ConversationMemory(system_prompt="test"),
            skills=reg,
            wrap_tool_output=False,
        )
        events = list(agent.stream_chat("找鼓手"))
        assert agent.last_backend == "langgraph"
        done = [e for e in events if e.get("type") == "done"][-1]
        assert "drum_01.jpg" in done["reply"]
        assert "ghost.jpg" not in done["reply"]
        tokens = "".join(e.get("text") or "" for e in events if e.get("type") == "token")
        assert tokens == done["reply"]


@pytest.mark.requires_langgraph
class TestAnswerBranchDirectNoFiles:
    """Checklist id: direct_no_files — echo/stats → decide prose → finalize."""

    checklist_id = "direct_no_files"

    def test_echo_uses_decide_direct_reply(self):
        reg = SkillRegistry()
        reg.register(_echo_skill())
        agent = ConversationalAgent(
            _scripted_chat(
                [
                    json.dumps({"tool": "echo", "args": {"v": "pong"}}),
                    "pong from graph",
                ]
            ),
            skills=reg,
        )
        res = agent.chat("echo please")
        assert agent.last_backend == "langgraph"
        assert res.reply == "pong from graph"
        assert res.tool_calls[0]["tool"] == "echo"
        non_system = [m for m in agent.memory.messages() if m["role"] != "system"]
        assert [m["role"] for m in non_system] == ["user", "assistant", "tool", "assistant"]
        assert json.loads(non_system[1]["content"]) == {
            "tool": "echo",
            "args": {"v": "pong"},
        }


@pytest.mark.requires_langgraph
class TestAnswerBranchPlainNoTools:
    """Checklist id: plain_no_tools — no skills / no tool loop → plain completion."""

    checklist_id = "plain_no_tools"

    def test_no_skills_uses_plain_completion(self):
        agent = ConversationalAgent(
            lambda _m: "I can search and select photos.",
            memory=ConversationMemory(system_prompt="test"),
            skills=None,
        )
        res = agent.chat("你能做什么")
        assert agent.last_backend == "langgraph"
        assert "search" in res.reply.lower() or "select" in res.reply.lower()
        assert res.tool_calls == []
