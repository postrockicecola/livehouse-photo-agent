"""Tests for the multi-turn conversational agent (services/agent/conversation).

The model is a scripted ``ChatFn`` so these run with no network: they verify memory
budgeting/eviction + rolling summary, multi-turn history threading, and the bounded
tool-calling protocol over a real :class:`SkillRegistry`.
"""
from __future__ import annotations

import json

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
    assert res.tool_calls == [
        {"tool": "echo", "args": {"v": "pong"}, "ok": True, "metadata": {}}
    ]
    assert res.working_memory.get("last_tool") == "echo"
    # Protocol chain: user → assistant(tool-call) → tool(result) → assistant(final).
    non_system = [m for m in agent.memory.messages() if m["role"] != "system"]
    assert [m["role"] for m in non_system] == ["user", "assistant", "tool", "assistant"]
    assert json.loads(non_system[1]["content"]) == {"tool": "echo", "args": {"v": "pong"}}
    assert non_system[2].get("name") == "echo"


def test_working_memory_tracks_selected_keys_as_last_files():
    class _Select:
        name = "gallery_select"
        description = "select"
        parameters = {"type": "object", "properties": {"files": {"type": "array"}}}

        def run(self, args):
            keys = list(args.get("files") or [])
            return SkillResult(
                ok=True,
                output=f"selected {len(keys)}",
                metadata={"selected_keys": keys, "ui_action": "reload_curation"},
            )

    reg = SkillRegistry()
    reg.register(_Select())
    scripted = iter(
        [
            '{"tool":"gallery_select","args":{"files":["a.jpg","b.jpg"]}}',
            "已选中 2 张",
        ]
    )
    agent = ConversationalAgent(lambda _m: next(scripted), skills=reg)
    res = agent.chat("标出来")
    assert res.working_memory.get("last_files") == ["a.jpg", "b.jpg"]
    assert res.working_memory.get("last_tool") == "gallery_select"


def test_second_decide_sees_assistant_tool_call_anchor():
    """Second decide must see the prior assistant decision, not a bare tool blob."""
    reg = SkillRegistry()
    reg.register(_echo_skill())
    seen_second: list[list[str]] = []

    def chat_fn(msgs):
        roles = [m["role"] for m in msgs if m["role"] != "system"]
        if "tool" in roles:
            seen_second.append(roles)
            return "done after seeing my own tool call"
        return json.dumps({"tool": "echo", "args": {"v": "1"}})

    agent = ConversationalAgent(chat_fn, skills=reg, max_tool_rounds=2)
    res = agent.chat("go")
    assert res.reply == "done after seeing my own tool call"
    assert seen_second, "expected a second decide after the tool ran"
    # ... user, assistant(decision), tool(result) — causal chain intact for decide #2
    assert seen_second[0][-3:] == ["user", "assistant", "tool"]


def test_lean_final_answer_keeps_prefs_and_working_memory():
    """Forced final-answer prompt must still see prefs + last_files (not only tool blobs)."""
    from services.agent.store import preferences_prompt_block

    reg = SkillRegistry()
    reg.register(_echo_skill())
    calls: list[list[dict]] = []

    def chat_fn(msgs):
        calls.append(list(msgs))
        if len(calls) == 1:
            return json.dumps({"tool": "echo", "args": {"v": "x"}})
        return "picked with your silhouette preference in mind"

    prefs = preferences_prompt_block({"avoid_silhouettes": "true"})
    mem = ConversationMemory(
        system_prompt=f"You are the Gallery copilot.\n\n{prefs}",
        max_tokens=10_000,
    )
    agent = ConversationalAgent(
        chat_fn,
        memory=mem,
        skills=reg,
        max_tool_rounds=1,  # one tool then forced lean final
        working_memory={"last_files": ["a.jpg", "b.jpg"], "last_tool": "gallery_search"},
    )
    res = agent.chat("选出几张")
    assert "silhouette" in res.reply or "preference" in res.reply
    assert len(calls) >= 2, "expected decide + lean final-answer model calls"
    lean = calls[-1]
    assert [m["role"] for m in lean] == ["system", "user"]
    sys_txt = lean[0]["content"]
    assert "SESSION CONTEXT" in sys_txt
    assert "avoid_silhouettes" in sys_txt
    assert "a.jpg" in sys_txt
    assert "AVAILABLE TOOLS" not in sys_txt  # still lean — no tool protocol dump


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


# --------------------------------------------------------------------- streaming


def _collect(events):
    """Split a stream_chat() event iterable into (tokens_joined, tool_calls, done)."""
    tokens, tools, done = [], [], None
    for ev in events:
        if ev["type"] == "token":
            tokens.append(ev["text"])
        elif ev["type"] == "tool_call":
            tools.append(ev)
        elif ev["type"] == "done":
            done = ev
    return "".join(tokens), tools, done


def test_stream_chat_real_streams_tokens_from_stream_fn():
    agent = ConversationalAgent(lambda msgs: "unused")
    stream_fn = lambda msgs: iter(["Hel", "lo ", "world"])
    text, tools, done = _collect(agent.stream_chat("hi", stream_fn=stream_fn))
    assert text == "Hello world"
    assert tools == []
    assert done is not None and done["reply"] == "Hello world"
    # The streamed reply is committed to memory (user + assistant).
    assert agent.memory.turn_count == 2


def test_stream_chat_chunks_when_no_stream_fn():
    # No stream_fn → the one-shot chat_fn result is chunked into token events.
    agent = ConversationalAgent(lambda msgs: "plain answer")
    text, tools, done = _collect(agent.stream_chat("hi"))
    assert text == "plain answer"
    assert done["reply"] == "plain answer"


def test_stream_chat_emits_tool_call_then_streams_forced_final():
    reg = SkillRegistry()
    reg.register(_echo_skill())
    # Decision rounds always re-request the same tool → repeat break → forced final,
    # which is the branch that streams via stream_fn.
    agent = ConversationalAgent(
        lambda msgs: '{"tool": "echo", "args": {"v": "pong"}}',
        skills=reg,
        max_tool_rounds=3,
    )
    stream_fn = lambda msgs: iter(["based ", "on ", "pong"])
    text, tools, done = _collect(agent.stream_chat("echo pong", stream_fn=stream_fn))
    assert len(tools) == 1 and tools[0]["tool"] == "echo" and tools[0]["ok"] is True
    assert text == "based on pong"
    assert done["tool_calls"][0]["tool"] == "echo"


def test_stream_chat_in_loop_answer_is_chunked_not_reissued():
    reg = SkillRegistry()
    reg.register(_echo_skill())
    scripted = iter([
        '{"tool": "echo", "args": {"v": "pong"}}',
        "the tool said pong",
    ])
    # stream_fn must NOT be consumed: the in-loop answer already exists and is chunked.
    def _boom(_msgs):
        raise AssertionError("stream_fn should not run when the model answers in-loop")

    agent = ConversationalAgent(lambda msgs: next(scripted), skills=reg)
    text, tools, done = _collect(agent.stream_chat("echo pong", stream_fn=_boom))
    assert text == "the tool said pong"
    assert len(tools) == 1


def test_stream_chat_buffers_stray_tool_json_into_fallback():
    # If the final generation is a stray tool-call JSON, it must never be shown; the
    # buffered head is replaced with the fallback prose instead.
    agent = ConversationalAgent(lambda msgs: "unused")
    stream_fn = lambda msgs: iter(['{"tool": ', '"echo", "args": {}}'])
    text, tools, done = _collect(agent.stream_chat("hi", stream_fn=stream_fn))
    assert "{" not in text  # no raw JSON leaked to the user
    assert done["reply"] == text
