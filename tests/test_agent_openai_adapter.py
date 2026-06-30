"""Tests for the OpenAI function-calling adapter (services/agent/openai_adapter).

A scripted ``chat_completion_fn`` returns OpenAI-shaped responses (with and without
``tool_calls``) so these verify the canonical tool-calling cycle end to end: tool specs
export, JSON-string argument parsing, tool-message threading, and the round limit.
"""
from __future__ import annotations

import json

from services.agent.openai_adapter import (
    _parse_arguments,
    run_openai_tool_loop,
    tools_for_openai,
)
from services.agent.skills.base import SkillRegistry, SkillResult


def _add_skill():
    class _Add:
        name = "add"
        description = "add two ints"
        parameters = {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        }

        def run(self, args):
            return SkillResult(ok=True, output=str(int(args["a"]) + int(args["b"])))

    return _Add()


def _registry():
    reg = SkillRegistry()
    reg.register(_add_skill())
    return reg


def test_tools_for_openai_shape():
    specs = tools_for_openai(_registry())
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["name"] == "add"


def test_parse_arguments_handles_json_string_and_dict():
    assert _parse_arguments('{"a": 1}') == {"a": 1}
    assert _parse_arguments({"a": 2}) == {"a": 2}
    assert _parse_arguments("not json") == {}
    assert _parse_arguments(None) == {}


def _assistant_tool_call(call_id, name, args):
    return {
        "choices": [{
            "message": {
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }
        }]
    }


def _assistant_text(text):
    return {"choices": [{"message": {"content": text, "tool_calls": []}}]}


def test_tool_loop_executes_and_returns_final_answer():
    reg = _registry()
    scripted = iter([
        _assistant_tool_call("call_1", "add", {"a": 2, "b": 3}),
        _assistant_text("the sum is 5"),
    ])

    res = run_openai_tool_loop(lambda msgs, tools: next(scripted), reg, [{"role": "user", "content": "2+3?"}])
    assert res.content == "the sum is 5"
    assert res.rounds == 2
    assert res.tool_calls == [{"id": "call_1", "name": "add", "args": {"a": 2, "b": 3}, "ok": True}]
    # The conversation has a properly threaded tool message answering call_1.
    tool_msgs = [m for m in res.messages if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "call_1"
    assert "5" in tool_msgs[0]["content"]


def test_tool_loop_plain_answer_no_tools():
    res = run_openai_tool_loop(
        lambda msgs, tools: _assistant_text("hi"),
        _registry(),
        [{"role": "user", "content": "hello"}],
    )
    assert res.content == "hi"
    assert res.tool_calls == []
    assert res.rounds == 1


def test_tool_loop_respects_round_limit():
    reg = _registry()
    # Model never stops calling tools → must hit the round cap.
    res = run_openai_tool_loop(
        lambda msgs, tools: _assistant_tool_call("c", "add", {"a": 1, "b": 1}),
        reg,
        [{"role": "user", "content": "loop"}],
        max_rounds=3,
    )
    assert res.rounds == 3
    assert "round limit" in res.content
    assert len(res.tool_calls) == 3


def test_tool_loop_accepts_bare_message_dict():
    # Some servers return just the message, not a full {choices:[...]} envelope.
    res = run_openai_tool_loop(
        lambda msgs, tools: {"content": "bare", "tool_calls": []},
        _registry(),
        [{"role": "user", "content": "x"}],
    )
    assert res.content == "bare"
