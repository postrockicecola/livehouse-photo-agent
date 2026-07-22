"""LangGraph subgraph for one Gallery / general chat turn.

Graph shape::

    START → decide → act ⟲ decide → answer → END
                   ↘ answer (plain / forced)

This is the **production** path for :class:`ConversationalAgent` when LangGraph is
available. The imperative loop in ``conversation.py`` remains the fallback
(``LIVEHOUSE_AGENT_RUNTIME=imperative``).

The compiled graph is also intended to be mounted as a **subgraph node** on the
platform graph in :mod:`services.agent.graph` (``gallery_chat``).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

# Imported lazily in compile to keep module import light for tests that only need helpers.


class ChatTurnState(TypedDict, total=False):
    user_text: str
    tool_calls: list[dict[str, Any]]
    observations: list[str]
    seen_keys: list[str]
    rounds_used: int
    max_rounds: int
    pending_call: Optional[dict[str, Any]]
    raw_model: Optional[str]
    direct_reply: Optional[str]
    force_answer: bool
    defer_answer: bool  # stream path: prepare answer inputs, skip model finalize
    reply: Optional[str]
    answer_messages: Optional[list[dict[str, str]]]
    done: bool
    backend: str


TurnHook = Callable[[dict[str, Any]], None]


def chat_runtime_preference() -> str:
    raw = (os.environ.get("LIVEHOUSE_AGENT_RUNTIME") or "langgraph").strip().lower()
    if raw in ("imperative", "loop", "legacy"):
        return "imperative"
    return "langgraph"


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401

        return True
    except ImportError:
        return False


def compile_chat_turn_graph(
    *,
    chat_fn: Callable[[list[dict[str, str]]], str],
    memory: Any,
    skills: Any,
    guardrails: Any,
    wrap_tool_output: bool,
    max_tool_result_chars: int,
    update_working_memory: Callable[[str, dict[str, Any], Any], None],
    record_tool_result: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook] = None,
    no_answer_fallback: str,
    final_answer_system: str,
    final_answer_nudge: str,
):
    """Compile the decide→act→answer turn graph (closures bind one agent instance)."""
    from langgraph.graph import END, START, StateGraph

    def _emit(ev: dict[str, Any]) -> None:
        if emit is None:
            return
        try:
            emit(ev)
        except Exception:
            logger.exception("chat turn emit failed")

    def decide(state: ChatTurnState) -> dict[str, Any]:
        max_rounds = int(state.get("max_rounds") or 0)
        rounds_used = int(state.get("rounds_used") or 0)
        tool_calls = list(state.get("tool_calls") or [])
        seen = list(state.get("seen_keys") or [])

        if skills is None or max_rounds <= 0:
            return {
                "pending_call": None,
                "direct_reply": None,
                "force_answer": False,
                "raw_model": None,
            }

        if rounds_used >= max_rounds:
            return {
                "pending_call": None,
                "direct_reply": None,
                "force_answer": bool(tool_calls),
                "raw_model": None,
            }

        raw = chat_fn(memory.messages())
        call = parse_tool_call(raw)
        if call is None:
            return {
                "pending_call": None,
                "direct_reply": raw,
                "force_answer": False,
                "raw_model": raw,
            }

        key = f"{call['tool']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
        if key in seen:
            return {
                "pending_call": None,
                "direct_reply": None,
                "force_answer": True,
                "raw_model": raw,
            }
        return {
            "pending_call": call,
            "direct_reply": None,
            "force_answer": False,
            "raw_model": raw,
        }

    def route_after_decide(state: ChatTurnState) -> Literal["act", "answer"]:
        if state.get("pending_call"):
            return "act"
        return "answer"

    def act(state: ChatTurnState) -> dict[str, Any]:
        call = state.get("pending_call") or {}
        tool = str(call.get("tool") or "")
        args = dict(call.get("args") or {})
        result = skills.dispatch(tool, args)
        update_working_memory(tool, args, result)
        record_tool_result(tool, result)
        obs = f"{tool} -> {json.dumps(result.to_observation(), ensure_ascii=False)}"
        tc = {
            "tool": tool,
            "args": args,
            "ok": result.ok,
            "metadata": getattr(result, "metadata", None) or {},
        }
        tool_calls = list(state.get("tool_calls") or [])
        tool_calls.append(tc)
        observations = list(state.get("observations") or [])
        observations.append(obs)
        seen = list(state.get("seen_keys") or [])
        key = f"{tool}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
        seen.append(key)
        _emit({"type": "tool_call", **tc})
        return {
            "tool_calls": tool_calls,
            "observations": observations,
            "seen_keys": seen,
            "rounds_used": int(state.get("rounds_used") or 0) + 1,
            "pending_call": None,
        }

    def answer(state: ChatTurnState) -> dict[str, Any]:
        user_text = str(state.get("user_text") or "")
        tool_calls = list(state.get("tool_calls") or [])
        observations = list(state.get("observations") or [])
        defer = bool(state.get("defer_answer"))
        direct = state.get("direct_reply")
        force = bool(state.get("force_answer"))

        if direct is not None and not force:
            # In-loop plain answer (may still be tool JSON if model misbehaved).
            if parse_tool_call(str(direct)) is not None:
                reply_src = no_answer_fallback
                answer_messages = None
            else:
                reply_src = str(direct)
                answer_messages = None
            if defer:
                return {
                    "reply": None,
                    "direct_reply": reply_src,
                    "answer_messages": None,
                    "done": True,
                    "backend": "langgraph",
                }
            reply = finalize(reply_src)
            _emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
            return {"reply": reply, "done": True, "backend": "langgraph"}

        if not tool_calls and not force:
            # No skills / never entered tool path → plain completion.
            if defer:
                return {
                    "reply": None,
                    "direct_reply": None,
                    "answer_messages": memory.messages(),
                    "done": True,
                    "backend": "langgraph",
                }
            final = chat_fn(memory.messages())
            if parse_tool_call(final) is not None:
                final = no_answer_fallback
            reply = finalize(final)
            _emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
            return {"reply": reply, "done": True, "backend": "langgraph"}

        # Tools ran / repeat / budget → forced lean final answer.
        joined = "\n".join(observations) if observations else "(no tool results)"
        messages = [
            {"role": "system", "content": final_answer_system},
            {
                "role": "user",
                "content": f"Question: {user_text}\n\nTool results:\n{joined}\n\n{final_answer_nudge}",
            },
        ]
        if defer:
            return {
                "reply": None,
                "direct_reply": None,
                "answer_messages": messages,
                "done": True,
                "backend": "langgraph",
            }
        final = force_final_answer(user_text, observations)
        reply = finalize(final)
        _emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
        return {"reply": reply, "answer_messages": messages, "done": True, "backend": "langgraph"}

    def route_after_act(state: ChatTurnState) -> Literal["decide", "answer"]:
        # Always go back to decide so the model can chain tools or answer.
        return "decide"

    g = StateGraph(ChatTurnState)
    g.add_node("decide", decide)
    g.add_node("act", act)
    g.add_node("answer", answer)
    g.add_edge(START, "decide")
    g.add_conditional_edges("decide", route_after_decide, {"act": "act", "answer": "answer"})
    g.add_conditional_edges("act", route_after_act, {"decide": "decide", "answer": "answer"})
    g.add_edge("answer", END)
    return g.compile()


def _initial_chat_state(
    *,
    user_text: str,
    max_tool_rounds: int,
    skills: Any,
    defer_answer: bool,
) -> ChatTurnState:
    return {
        "user_text": user_text,
        "tool_calls": [],
        "observations": [],
        "seen_keys": [],
        "rounds_used": 0,
        "max_rounds": max_tool_rounds if skills is not None else 0,
        "pending_call": None,
        "direct_reply": None,
        "force_answer": False,
        "defer_answer": defer_answer,
        "reply": None,
        "answer_messages": None,
        "done": False,
        "backend": "langgraph",
    }


def run_chat_turn(
    *,
    user_text: str,
    max_tool_rounds: int,
    chat_fn: Callable[[list[dict[str, str]]], str],
    memory: Any,
    skills: Any,
    guardrails: Any,
    wrap_tool_output: bool,
    max_tool_result_chars: int,
    update_working_memory: Callable[[str, dict[str, Any], Any], None],
    record_tool_result: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook],
    no_answer_fallback: str,
    final_answer_system: str,
    final_answer_nudge: str,
    defer_answer: bool = False,
) -> ChatTurnState:
    app = compile_chat_turn_graph(
        chat_fn=chat_fn,
        memory=memory,
        skills=skills,
        guardrails=guardrails,
        wrap_tool_output=wrap_tool_output,
        max_tool_result_chars=max_tool_result_chars,
        update_working_memory=update_working_memory,
        record_tool_result=record_tool_result,
        finalize=finalize,
        force_final_answer=force_final_answer,
        parse_tool_call=parse_tool_call,
        emit=emit,
        no_answer_fallback=no_answer_fallback,
        final_answer_system=final_answer_system,
        final_answer_nudge=final_answer_nudge,
    )
    init = _initial_chat_state(
        user_text=user_text,
        max_tool_rounds=max_tool_rounds,
        skills=skills,
        defer_answer=defer_answer,
    )
    return app.invoke(init)  # type: ignore[return-value]


def iter_chat_turn_updates(
    *,
    user_text: str,
    max_tool_rounds: int,
    chat_fn: Callable[[list[dict[str, str]]], str],
    memory: Any,
    skills: Any,
    guardrails: Any,
    wrap_tool_output: bool,
    max_tool_result_chars: int,
    update_working_memory: Callable[[str, dict[str, Any], Any], None],
    record_tool_result: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook],
    no_answer_fallback: str,
    final_answer_system: str,
    final_answer_nudge: str,
    defer_answer: bool = True,
):
    """Yield ``(node_name, partial_state)`` as the chat subgraph runs (for SSE)."""
    app = compile_chat_turn_graph(
        chat_fn=chat_fn,
        memory=memory,
        skills=skills,
        guardrails=guardrails,
        wrap_tool_output=wrap_tool_output,
        max_tool_result_chars=max_tool_result_chars,
        update_working_memory=update_working_memory,
        record_tool_result=record_tool_result,
        finalize=finalize,
        force_final_answer=force_final_answer,
        parse_tool_call=parse_tool_call,
        emit=emit,
        no_answer_fallback=no_answer_fallback,
        final_answer_system=final_answer_system,
        final_answer_nudge=final_answer_nudge,
    )
    init = _initial_chat_state(
        user_text=user_text,
        max_tool_rounds=max_tool_rounds,
        skills=skills,
        defer_answer=defer_answer,
    )
    for update in app.stream(init, stream_mode="updates"):
        if not isinstance(update, dict):
            continue
        for node_name, partial in update.items():
            yield str(node_name), dict(partial or {})


GALLERY_CHAT_MAPPING = {
    "ConversationMemory": "closed over by decide/answer nodes",
    "model tool JSON": "node: decide",
    "SkillRegistry.dispatch": "node: act",
    "forced / plain final answer": "node: answer",
    "ConversationalAgent.chat": "run_chat_turn (LangGraph primary)",
    "platform mount": "services.agent.graph.compile_agent_platform_graph → gallery_chat subgraph",
}
