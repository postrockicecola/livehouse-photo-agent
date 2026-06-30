"""Tests for the multi-turn conversational agent (services/agent/conversation).

The model is a scripted ``ChatFn`` so these run with no network: they verify memory
budgeting/eviction + rolling summary, multi-turn history threading, and the bounded
tool-calling protocol over a real :class:`SkillRegistry`.
"""
from __future__ import annotations

from services.agent.conversation import (
    ConversationalAgent,
    ConversationMemory,
    _parse_tool_call,
    approx_tokens,
)
from services.agent.skills.base import SkillRegistry, SkillResult


# ------------------------------------------------------------------------- memory


def test_memory_keeps_system_and_threads_turns():
    mem = ConversationMemory(system_prompt="you are helpful", max_tokens=10_000)
    mem.add_user("hi")
    mem.add_assistant("hello")
    mem.add_user("bye")
    msgs = mem.messages()
    assert msgs[0] == {"role": "system", "content": "you are helpful"}
    assert [m["role"] for m in msgs[1:]] == ["user", "assistant", "user"]
    assert mem.turn_count == 3


def test_memory_evicts_oldest_when_over_budget():
    # Tiny budget forces eviction; no summarizer → old turns are dropped.
    mem = ConversationMemory(system_prompt="sys", max_tokens=40)
    for i in range(20):
        mem.add_user(f"message number {i} with some padding text")
    # Budget enforced: far fewer than 20 turns remain, but at least the latest.
    assert 1 <= mem.turn_count < 20
    assert mem.messages()[0]["role"] == "system"


def test_memory_summarizes_evicted_turns():
    seen = {}

    def summarizer(msgs):
        seen["count"] = seen.get("count", 0) + 1
        return f"summary-of-{len(msgs)}"

    mem = ConversationMemory(system_prompt="sys", max_tokens=40, summarizer=summarizer)
    for i in range(20):
        mem.add_user(f"message number {i} with some padding text")
    assert mem.summary is not None
    # The rolling summary is injected as a system message after the main system prompt.
    sys_msgs = [m for m in mem.messages() if m["role"] == "system"]
    assert any("Summary of earlier conversation" in m["content"] for m in sys_msgs)


def test_approx_tokens_monotonic():
    assert approx_tokens("a") >= 1
    assert approx_tokens("a" * 400) > approx_tokens("a" * 40)


# --------------------------------------------------------------------- tool parsing


def test_parse_tool_call_variants():
    assert _parse_tool_call('{"tool": "python_exec", "args": {"code": "x"}}') == {
        "tool": "python_exec",
        "args": {"code": "x"},
    }
    assert _parse_tool_call('```json\n{"tool":"t","args":{}}\n```') == {"tool": "t", "args": {}}
    assert _parse_tool_call("just a plain answer") is None
    assert _parse_tool_call('{"no_tool": 1}') is None


# --------------------------------------------------------------------- chat loop


def _echo_skill():
    class _Echo:
        name = "echo"
        description = "echo back"
        parameters = {"type": "object", "properties": {"v": {"type": "string"}}}

        def run(self, args):
            return SkillResult(ok=True, output=str(args.get("v", "")))

    return _Echo()


def test_chat_plain_reply_without_tools():
    agent = ConversationalAgent(lambda msgs: "hello there")
    res = agent.chat("hi")
    assert res.reply == "hello there"
    assert res.tool_calls == []
    # Both turns are now in memory.
    assert agent.memory.turn_count == 2


def test_chat_runs_tool_then_answers():
    reg = SkillRegistry()
    reg.register(_echo_skill())

    # Scripted model: first emit a tool call, then a final text answer.
    scripted = iter([
        '{"tool": "echo", "args": {"v": "pong"}}',
        "the tool said pong",
    ])

    agent = ConversationalAgent(lambda msgs: next(scripted), skills=reg)
    res = agent.chat("please echo pong")
    assert res.reply == "the tool said pong"
    assert res.tool_calls == [{"tool": "echo", "args": {"v": "pong"}, "ok": True}]
    # A tool-role message carrying the observation is in memory.
    roles = [m["role"] for m in agent.memory.messages()]
    assert "tool" in roles


def test_chat_breaks_on_repeated_identical_tool_call():
    reg = SkillRegistry()
    reg.register(_echo_skill())
    # Model always re-requests the SAME call → repeat-detection runs it once, then
    # forces a final answer instead of looping until the round limit.
    agent = ConversationalAgent(
        lambda msgs: '{"tool": "echo", "args": {"v": "loop"}}',
        skills=reg,
        max_tool_rounds=3,
    )
    res = agent.chat("loop forever")
    assert len(res.tool_calls) == 1  # identical repeat not re-executed
    assert "couldn't compose a final answer" in res.reply  # forced-final fallback


def test_chat_distinct_tool_calls_respect_round_limit():
    reg = SkillRegistry()
    reg.register(_echo_skill())
    # Distinct args each round → not deduped; bounded by max_tool_rounds.
    n = iter(range(100))
    agent = ConversationalAgent(
        lambda msgs: f'{{"tool": "echo", "args": {{"v": "v{next(n)}"}}}}',
        skills=reg,
        max_tool_rounds=2,
    )
    res = agent.chat("loop with new args")
    assert len(res.tool_calls) == 2  # bounded by the round limit


def test_chat_unknown_tool_is_reported_not_fatal():
    reg = SkillRegistry()
    scripted = iter([
        '{"tool": "ghost", "args": {}}',
        "sorry, that tool is unavailable",
    ])
    agent = ConversationalAgent(lambda msgs: next(scripted), skills=reg)
    res = agent.chat("use ghost")
    assert res.reply == "sorry, that tool is unavailable"
    assert res.tool_calls[0]["ok"] is False
