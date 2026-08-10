"""LangGraph subgraph for one Gallery / general chat turn.

Graph shape::

    START → plan ─┬→ execute_plan → answer → END
                  └→ decide → act ⟲ decide → answer → END
                                ↘ answer (plain / forced)

This is the **production** path for :class:`ConversationalAgent`. The imperative
loop in ``conversation.py`` is legacy opt-in only
(``LIVEHOUSE_AGENT_RUNTIME=imperative``).

:class:`ConversationalAgent` compiles the graph once per instance and passes the
compiled app into :func:`run_chat_turn` / :func:`iter_chat_turn_updates`.
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
    selection_goal: Optional[dict[str, Any]]
    planned_route: Optional[dict[str, Any]]
    plan_rule_id: Optional[str]


TurnHook = Callable[[dict[str, Any]], None]

# answer() branch coverage checklist — keep in sync with tests marked
# ``requires_langgraph`` in ``tests/test_agent_conversation_graph.py``.
# Each entry's ``id`` must have a dedicated TestAnswerBranch* class / test.
# When adding a branch: append here, wire ``select_answer_branch``, add a test.
ANSWER_BRANCH_CHECKLIST: tuple[dict[str, str], ...] = (
    {
        "id": "lean_refs",
        "when": (
            "tool results expose files/selected_keys (ignores decide prose), "
            "or force_answer / budget after tools"
        ),
        "path": "lean PHOTO REFS prompt → finalize ({ref_N} resolve + groundedness)",
    },
    {
        "id": "direct_no_files",
        "when": (
            "decide returned plain text and lean_refs does not apply "
            "(e.g. echo/stats with no cite files, or first-turn prose)"
        ),
        "path": "direct_reply → finalize (text scan only; no placeholder index)",
    },
    {
        "id": "plain_no_tools",
        "when": "no tool_calls and no direct_reply (skills disabled / max_rounds=0)",
        "path": "plain chat_fn(memory) → finalize",
    },
)

AnswerBranchId = Literal["lean_refs", "direct_no_files", "plain_no_tools"]


def tool_calls_have_cite_files(tool_calls: list[dict[str, Any]] | None) -> bool:
    """True when any tool result exposes files the final answer may cite."""
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        for key in ("files", "selected_keys"):
            vals = meta.get(key) or []
            if isinstance(vals, list) and any(str(x or "").strip() for x in vals):
                return True
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        vals = args.get("files") or []
        if isinstance(vals, list) and any(str(x or "").strip() for x in vals):
            return True
    return False


def select_answer_branch(
    *,
    has_direct: bool,
    force: bool,
    tool_calls: list[dict[str, Any]] | None,
) -> AnswerBranchId:
    """Pure branch picker for :func:`answer` — unit-testable without LangGraph runtime.

    Mirrors ``ANSWER_BRANCH_CHECKLIST``. Changing this without updating the
    checklist + ``requires_langgraph`` tests is a review smell.
    """
    prefer_lean_refs = bool(tool_calls) and tool_calls_have_cite_files(tool_calls)
    if has_direct and not force and not prefer_lean_refs:
        return "direct_no_files"
    if not tool_calls and not force:
        return "plain_no_tools"
    return "lean_refs"


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
    record_tool_decision: Callable[[str, dict[str, Any]], None],
    record_tool_observation: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    build_final_answer_messages: Callable[[str, list[str]], list[dict[str, str]]],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook] = None,
    no_answer_fallback: str,
    looks_like_tool_intent: Optional[Callable[[str], bool]] = None,
    merge_tool_args: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    execute_planned_route: Optional[
        Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    ] = None,
):
    """Compile the plan→decide/act→answer turn graph (closures bind one agent instance)."""
    from langgraph.graph import END, START, StateGraph

    def _emit(ev: dict[str, Any]) -> None:
        if emit is None:
            return
        try:
            emit(ev)
        except Exception:
            logger.exception("chat turn emit failed")

    def _toolish(text: str) -> bool:
        if looks_like_tool_intent is None:
            return False
        try:
            return bool(looks_like_tool_intent(text))
        except Exception:
            return False

    def plan(state: ChatTurnState) -> dict[str, Any]:
        """Normalize supported semantic requests before any model tool decision."""
        if skills is None or execute_planned_route is None:
            return {
                "selection_goal": None,
                "planned_route": None,
                "plan_rule_id": None,
            }
        from services.agent.intent_router import semantic_selection_route

        match = semantic_selection_route(str(state.get("user_text") or ""))
        if match is None:
            return {
                "selection_goal": None,
                "planned_route": None,
                "plan_rule_id": None,
            }
        calls = [
            {"tool": call.tool, "args": dict(call.args)}
            for call in match.calls
        ]
        goal = None
        if calls:
            raw_goal = calls[0]["args"].get("selection_goal")
            if isinstance(raw_goal, dict):
                goal = dict(raw_goal)
        return {
            "selection_goal": goal,
            "planned_route": {
                "rule_id": match.rule_id,
                "calls": calls,
                "select_after_search": match.select_after_search,
            },
            "plan_rule_id": match.rule_id,
        }

    def route_after_plan(state: ChatTurnState) -> Literal["execute_plan", "decide"]:
        if state.get("planned_route") and execute_planned_route is not None:
            return "execute_plan"
        return "decide"

    def execute_plan(state: ChatTurnState) -> dict[str, Any]:
        """Execute a semantic plan deterministically, then skip tool-selection LLM calls."""
        route = state.get("planned_route")
        if not isinstance(route, dict) or execute_planned_route is None:
            return {"force_answer": False}
        tool_calls, observations = execute_planned_route(route)
        return {
            "tool_calls": list(tool_calls),
            "observations": list(observations),
            "force_answer": True,
            "pending_call": None,
        }

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
            # Broken tool JSON should not leak to the user as prose.
            if _toolish(raw):
                return {
                    "pending_call": None,
                    "direct_reply": no_answer_fallback if not tool_calls else None,
                    "force_answer": bool(tool_calls),
                    "raw_model": raw,
                }
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
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from services.agent.tool_protocol import (
            MULTI_TOOL,
            all_read_only,
            expand_tool_calls,
        )

        call = state.get("pending_call") or {}
        sub_calls = expand_tool_calls(call)
        if not sub_calls:
            return {"pending_call": None}

        def _merge(tool: str, args: dict[str, Any]) -> dict[str, Any]:
            merged = dict(args)
            if merge_tool_args is not None:
                try:
                    merged = dict(merge_tool_args(tool, merged) or merged)
                except Exception:
                    logger.exception("merge_tool_args failed; using raw args")
            return merged

        def _dispatch(tool: str, merged: dict[str, Any]):
            return skills.dispatch(tool, merged)

        prepared = [(str(c["tool"]), _merge(str(c["tool"]), dict(c.get("args") or {}))) for c in sub_calls]
        # Parallel dispatch for multi read-only batches; memory/events stay serial.
        dispatched: list[tuple[str, dict[str, Any], Any]]
        if (
            len(prepared) > 1
            and all_read_only([{"tool": t, "args": a} for t, a in prepared])
            and str(call.get("tool") or "") == MULTI_TOOL
        ):
            slots: list[tuple[str, dict[str, Any], Any] | None] = [None] * len(prepared)
            with ThreadPoolExecutor(max_workers=min(4, len(prepared))) as pool:
                futs = {pool.submit(_dispatch, t, a): i for i, (t, a) in enumerate(prepared)}
                for fut in as_completed(futs):
                    i = futs[fut]
                    t, a = prepared[i]
                    slots[i] = (t, a, fut.result())
            dispatched = [s for s in slots if s is not None]
        else:
            dispatched = [(t, a, _dispatch(t, a)) for t, a in prepared]

        tool_calls = list(state.get("tool_calls") or [])
        observations = list(state.get("observations") or [])
        seen = list(state.get("seen_keys") or [])
        for tool, merged, result in dispatched:
            # Causal chain: assistant(decision) before tool observation.
            record_tool_decision(tool, merged)
            update_working_memory(tool, merged, result)
            record_tool_observation(tool, result)
            tc = {
                "tool": tool,
                "args": merged,
                "ok": result.ok,
                "metadata": getattr(result, "metadata", None) or {},
            }
            obs = f"{tool} -> {json.dumps(result.to_observation(), ensure_ascii=False)}"
            tool_calls.append(tc)
            observations.append(obs)
            seen.append(f"{tool}:{json.dumps(merged, sort_keys=True, ensure_ascii=False)}")
            _emit({"type": "tool_call", **tc})

        # One decide-round budget per model emission (multi batch counts as one).
        return {
            "tool_calls": tool_calls,
            "observations": observations,
            "seen_keys": seen,
            "rounds_used": int(state.get("rounds_used") or 0) + 1,
            "pending_call": None,
        }

    def answer(state: ChatTurnState) -> dict[str, Any]:
        """Finalize one turn. Branches: see ``ANSWER_BRANCH_CHECKLIST`` above."""
        user_text = str(state.get("user_text") or "")
        tool_calls = list(state.get("tool_calls") or [])
        observations = list(state.get("observations") or [])
        defer = bool(state.get("defer_answer"))
        direct = state.get("direct_reply")
        force = bool(state.get("force_answer"))
        branch = select_answer_branch(
            has_direct=direct is not None,
            force=force,
            tool_calls=tool_calls,
        )

        if branch == "direct_no_files":
            # In-loop plain answer (may still be tool JSON if model misbehaved).
            if parse_tool_call(str(direct)) is not None:
                reply_src = no_answer_fallback
            else:
                reply_src = str(direct)
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

        if branch == "plain_no_tools":
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

        # branch == "lean_refs": file-cite / force / budget → lean PHOTO REFS final.
        messages = build_final_answer_messages(user_text, observations)
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
    g.add_node("plan", plan)
    g.add_node("execute_plan", execute_plan)
    g.add_node("decide", decide)
    g.add_node("act", act)
    g.add_node("answer", answer)
    g.add_edge(START, "plan")
    g.add_conditional_edges(
        "plan",
        route_after_plan,
        {"execute_plan": "execute_plan", "decide": "decide"},
    )
    g.add_edge("execute_plan", "answer")
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
        "selection_goal": None,
        "planned_route": None,
        "plan_rule_id": None,
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
    record_tool_decision: Callable[[str, dict[str, Any]], None],
    record_tool_observation: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    build_final_answer_messages: Callable[[str, list[str]], list[dict[str, str]]],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook],
    no_answer_fallback: str,
    looks_like_tool_intent: Optional[Callable[[str], bool]] = None,
    merge_tool_args: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    execute_planned_route: Optional[
        Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    ] = None,
    defer_answer: bool = False,
    app: Any = None,
) -> ChatTurnState:
    if app is None:
        app = compile_chat_turn_graph(
            chat_fn=chat_fn,
            memory=memory,
            skills=skills,
            guardrails=guardrails,
            wrap_tool_output=wrap_tool_output,
            max_tool_result_chars=max_tool_result_chars,
            update_working_memory=update_working_memory,
            record_tool_decision=record_tool_decision,
            record_tool_observation=record_tool_observation,
            finalize=finalize,
            force_final_answer=force_final_answer,
            build_final_answer_messages=build_final_answer_messages,
            parse_tool_call=parse_tool_call,
            emit=emit,
            no_answer_fallback=no_answer_fallback,
            looks_like_tool_intent=looks_like_tool_intent,
            merge_tool_args=merge_tool_args,
            execute_planned_route=execute_planned_route,
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
    record_tool_decision: Callable[[str, dict[str, Any]], None],
    record_tool_observation: Callable[[str, Any], None],
    finalize: Callable[[str], str],
    force_final_answer: Callable[[str, list[str]], str],
    build_final_answer_messages: Callable[[str, list[str]], list[dict[str, str]]],
    parse_tool_call: Callable[[str], Optional[dict[str, Any]]],
    emit: Optional[TurnHook],
    no_answer_fallback: str,
    looks_like_tool_intent: Optional[Callable[[str], bool]] = None,
    merge_tool_args: Optional[Callable[[str, dict[str, Any]], dict[str, Any]]] = None,
    execute_planned_route: Optional[
        Callable[[dict[str, Any]], tuple[list[dict[str, Any]], list[str]]]
    ] = None,
    defer_answer: bool = True,
    app: Any = None,
):
    """Yield ``(node_name, partial_state)`` as the chat subgraph runs (for SSE)."""
    if app is None:
        app = compile_chat_turn_graph(
            chat_fn=chat_fn,
            memory=memory,
            skills=skills,
            guardrails=guardrails,
            wrap_tool_output=wrap_tool_output,
            max_tool_result_chars=max_tool_result_chars,
            update_working_memory=update_working_memory,
            record_tool_decision=record_tool_decision,
            record_tool_observation=record_tool_observation,
            finalize=finalize,
            force_final_answer=force_final_answer,
            build_final_answer_messages=build_final_answer_messages,
            parse_tool_call=parse_tool_call,
            emit=emit,
            no_answer_fallback=no_answer_fallback,
            looks_like_tool_intent=looks_like_tool_intent,
            merge_tool_args=merge_tool_args,
            execute_planned_route=execute_planned_route,
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
    "structured selection goal": "node: plan",
    "deterministic planned tools": "node: execute_plan",
    "model tool JSON": "node: decide",
    "SkillRegistry.dispatch": "node: act",
    "forced / plain final answer": "node: answer",
    "ConversationalAgent.chat": "run_chat_turn (LangGraph primary)",
}
