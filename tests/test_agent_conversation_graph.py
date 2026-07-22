"""Gallery chat LangGraph subgraph + platform mount."""
from __future__ import annotations

import json

import pytest

from services.agent.conversation import ConversationalAgent
from services.agent.conversation_graph import (
    GALLERY_CHAT_MAPPING,
    compile_chat_turn_graph,
    langgraph_available,
)
from services.agent.graph import compile_agent_platform_graph
from services.agent.skills.base import SkillRegistry, SkillResult
from services.agent.types import AgentConfig, AgentResult, Candidate


pytestmark = pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")


def _echo_skill():
    class _Echo:
        name = "echo"
        description = "echo"
        parameters = {"type": "object", "properties": {"v": {"type": "string"}}}

        def run(self, args):
            return SkillResult(ok=True, output=str(args.get("v", "")))

    return _Echo()


def test_gallery_chat_mapping_mentions_subgraph():
    assert "platform mount" in GALLERY_CHAT_MAPPING


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


def test_conversational_agent_uses_langgraph_backend(monkeypatch):
    monkeypatch.delenv("LIVEHOUSE_AGENT_RUNTIME", raising=False)
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


def test_platform_graph_routes_to_gallery_chat_subgraph(monkeypatch):
    monkeypatch.delenv("LIVEHOUSE_AGENT_RUNTIME", raising=False)
    reg = SkillRegistry()
    reg.register(_echo_skill())
    agent = ConversationalAgent(
        _scripted_chat(
            [
                json.dumps({"tool": "echo", "args": {"v": "x"}}),
                "done",
            ]
        ),
        skills=reg,
    )
    # User turn is recorded before the subgraph (mirrors ConversationalAgent.chat).
    agent.memory.add_user("hi")

    chat_sub = compile_chat_turn_graph(**agent._graph_kwargs())

    def curation_runner(cands):
        return AgentResult(selected=[], candidates=cands, steps=[], metrics={"backend": "langgraph"})

    platform = compile_agent_platform_graph(
        curation_runner=curation_runner,
        chat_subgraph=chat_sub,
    )
    names = {n for n in platform.get_graph().nodes if not str(n).startswith("__")}
    assert "gallery_chat" in names
    assert "curation" in names

    out = platform.invoke({"intent": "chat", "user_text": "hi"})
    assert out.get("chat_reply") == "done"
    assert out.get("backend") == "langgraph"
    assert out.get("chat_tool_calls")


def test_platform_graph_routes_to_curation():
    def curation_runner(cands):
        return AgentResult(
            selected=["a"],
            candidates=cands,
            steps=[],
            metrics={"backend": "langgraph", "selected_count": 1},
        )

    # Minimal chat subgraph stub (never invoked on curate intent).
    from langgraph.graph import END, START, StateGraph

    class _S(dict):
        pass

    sg = StateGraph(dict)
    sg.add_node("noop", lambda s: s)
    sg.add_edge(START, "noop")
    sg.add_edge("noop", END)
    chat_sub = sg.compile()

    platform = compile_agent_platform_graph(
        curation_runner=curation_runner,
        chat_subgraph=chat_sub,
    )
    cands = [Candidate(image_id="a", image_path="/tmp/a.jpg", features={"fast_score": 1.0})]
    out = platform.invoke({"intent": "curate", "candidates": cands})
    assert out["curation_result"].selected == ["a"]
