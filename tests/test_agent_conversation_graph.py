"""Gallery chat LangGraph subgraph."""
from __future__ import annotations

import json

import pytest

from services.agent.conversation import ConversationalAgent
from services.agent.conversation_graph import (
    GALLERY_CHAT_MAPPING,
    langgraph_available,
)
from services.agent.skills.base import SkillRegistry, SkillResult


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
    assert "ConversationalAgent.chat" in GALLERY_CHAT_MAPPING
    assert "node: decide" in GALLERY_CHAT_MAPPING.values()


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
