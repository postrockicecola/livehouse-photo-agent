"""LangGraph production runtime for curation (ReAct: plan → act → reflect).

This is the **primary** agent loop when ``langgraph`` is installed. The imperative
``while`` loop in :mod:`services.agent.loop` remains as an explicit fallback
(``LIVEHOUSE_AGENT_RUNTIME=imperative`` or ImportError).

Graph shape (interview-visible)::

    START → plan → act → reflect → (finalize? END : plan)

Nodes are intentionally separate so the runtime is a real StateGraph, not a single
mega-node wrapping the old while-loop.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Literal, Optional, TypedDict  # noqa: F401 — Literal used by platform graph

from services.agent.planner import HeuristicPlanner, Planner
from services.agent.reflection import reflect as default_reflect
from services.agent.tools import ToolRegistry
from services.agent.types import (
    ActionType,
    AgentConfig,
    AgentResult,
    AgentState,
    AgentStep,
    Candidate,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

StepHook = Callable[[AgentStep, AgentState], None]
ReflectFn = Callable[..., Any]

LANGGRAPH_MAPPING = {
    "AgentState": "CurationGraphState.agent_state",
    "planner.next_action": "node: plan",
    "tools.dispatch": "node: act",
    "reflect / escalate": "node: reflect",
    "FINALIZE / max_steps": "conditional edge → END",
    "AgentStep + step_hook": "emitted after reflect (job_events timeline)",
    "Gallery chat turn": "conversation_graph decide→act→answer (subgraph)",
    "Platform router": "compile_agent_platform_graph → curation | gallery_chat",
    "MultiAgentOrchestrator": "fan-out of compiled subgraphs / Send API",
}


class CurationGraphState(TypedDict, total=False):
    agent_state: AgentState
    steps: list[AgentStep]
    pending_call: Optional[ToolCall]
    last_result: Optional[ToolResult]
    fallback_calls: int
    done: bool
    last_step: Optional[AgentStep]


def runtime_preference() -> str:
    """``langgraph`` (default) or ``imperative`` via ``LIVEHOUSE_AGENT_RUNTIME``."""
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


def build_metrics(state: AgentState, steps: list[AgentStep], fallback_calls: int, *, backend: str) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for s in steps:
        action_counts[s.call.action.value] = action_counts.get(s.call.action.value, 0) + 1
        source_counts[s.call.source] = source_counts.get(s.call.source, 0) + 1
    llm_steps = source_counts.get("llm", 0)
    llm_total = llm_steps + fallback_calls
    return {
        "steps": len(steps),
        "inferences_used": state.inferences_used,
        "max_inferences": state.config.max_inferences,
        "budget_exhausted": state.budget_exhausted(),
        "escalations": state.escalations,
        "llm_fallback_calls": fallback_calls,
        "llm_decision_rate": (llm_steps / llm_total) if llm_total else None,
        "candidates_total": len(state.candidates),
        "candidates_analyzed": state.analyzed_count(),
        "selected_count": len(state.selected),
        "action_counts": action_counts,
        "planner_source_counts": source_counts,
        "backend": backend,
        "langgraph_mapping": dict(LANGGRAPH_MAPPING),
    }


def compile_curation_graph(
    *,
    tools: ToolRegistry,
    config: AgentConfig,
    planner: Planner,
    reflect_fn: ReflectFn = default_reflect,
    step_hook: Optional[StepHook] = None,
    checkpointer: Any = None,
):
    """Build and compile the plan→act→reflect StateGraph."""
    from langgraph.graph import END, START, StateGraph

    def plan(state: CurationGraphState) -> dict[str, Any]:
        st = state["agent_state"]
        if st.step_index >= config.max_steps:
            logger.warning("agent hit max_steps=%s; forcing finalize", config.max_steps)
            call = ToolCall(action=ActionType.FINALIZE, reason="max_steps_reached", source="loop_guard")
        else:
            call = planner.next_action(st)
        return {"pending_call": call, "done": False}

    def act(state: CurationGraphState) -> dict[str, Any]:
        st = state["agent_state"]
        call = state.get("pending_call")
        if call is None:
            call = ToolCall(action=ActionType.FINALIZE, reason="missing_plan", source="loop_guard")
        result = tools.dispatch(st, call)
        st.step_index += 1
        st.inferences_used += result.inference_cost
        fb = int(state.get("fallback_calls") or 0)
        if call.source == "llm_fallback":
            fb += 1
        step = AgentStep(index=st.step_index, call=call, result=result, reflection=None)
        steps = list(state.get("steps") or [])
        steps.append(step)
        # step_hook fires once in reflect (after optional escalation note), matching the
        # imperative loop's "record AgentStep then emit" order.
        return {
            "agent_state": st,
            "steps": steps,
            "last_result": result,
            "last_step": step,
            "fallback_calls": fb,
            "pending_call": call,
        }

    def reflect_node(state: CurationGraphState) -> dict[str, Any]:
        st = state["agent_state"]
        call = state.get("pending_call")
        result = state.get("last_result")
        steps = list(state.get("steps") or [])
        reflection_note: Optional[str] = None

        if (
            call is not None
            and result is not None
            and call.action == ActionType.ANALYZE
            and result.inference_cost > 0
        ):
            cand = st.candidates.get(call.image_id or "")
            if cand is not None:
                verdict = reflect_fn(cand, config)
                if verdict.escalate and not st.budget_exhausted():
                    st.pending_escalations.append(cand.image_id)
                    st.escalations += 1
                    reflection_note = f"queue escalation: {verdict.reason}"
                elif not verdict.valid:
                    reflection_note = f"invalid output (no retry): {verdict.reason}"

        if steps:
            last = steps[-1]
            if reflection_note:
                steps[-1] = AgentStep(
                    index=last.index,
                    call=last.call,
                    result=last.result,
                    reflection=reflection_note,
                )
            if step_hook is not None:
                try:
                    step_hook(steps[-1], st)
                except Exception:
                    logger.exception("agent step_hook failed (step #%s)", last.index)

        done = bool(
            call is not None
            and result is not None
            and call.action == ActionType.FINALIZE
            and result.ok
        )
        return {
            "agent_state": st,
            "steps": steps,
            "done": done,
            "last_step": steps[-1] if steps else state.get("last_step"),
        }

    def route_after_reflect(state: CurationGraphState) -> Literal["plan", "__end__"]:
        return "__end__" if state.get("done") else "plan"

    g = StateGraph(CurationGraphState)
    g.add_node("plan", plan)
    g.add_node("act", act)
    g.add_node("reflect", reflect_node)
    g.add_edge(START, "plan")
    g.add_edge("plan", "act")
    g.add_edge("act", "reflect")
    g.add_conditional_edges("reflect", route_after_reflect, {"plan": "plan", "__end__": END})

    if checkpointer is not None:
        return g.compile(checkpointer=checkpointer)
    return g.compile()


def run_curation_graph(
    candidates: list[Candidate],
    *,
    tools: ToolRegistry,
    config: AgentConfig,
    planner: Optional[Planner] = None,
    reflect_fn: ReflectFn = default_reflect,
    step_hook: Optional[StepHook] = None,
    metrics_hook: Optional[Callable[[dict[str, Any]], None]] = None,
    thread_id: Optional[str] = None,
    use_checkpointer: bool = False,
) -> AgentResult:
    """Execute curation on the LangGraph runtime (or raise ImportError)."""
    planner = planner or HeuristicPlanner()
    checkpointer = None
    invoke_config: dict[str, Any] | None = None
    if use_checkpointer or thread_id:
        from langgraph.checkpoint.memory import MemorySaver

        checkpointer = MemorySaver()
        invoke_config = {"configurable": {"thread_id": thread_id or "curation"}}

    app = compile_curation_graph(
        tools=tools,
        config=config,
        planner=planner,
        reflect_fn=reflect_fn,
        step_hook=step_hook,
        checkpointer=checkpointer,
    )
    init: CurationGraphState = {
        "agent_state": AgentState.from_candidates(candidates, config),
        "steps": [],
        "pending_call": None,
        "last_result": None,
        "fallback_calls": 0,
        "done": False,
        "last_step": None,
    }
    final = app.invoke(init, config=invoke_config) if invoke_config else app.invoke(init)
    st: AgentState = final["agent_state"]
    steps = list(final.get("steps") or [])
    fb = int(final.get("fallback_calls") or 0)
    metrics = build_metrics(st, steps, fb, backend="langgraph")
    if metrics_hook is not None:
        try:
            metrics_hook(metrics)
        except Exception:
            logger.exception("agent metrics_hook failed")
    return AgentResult(
        selected=list(st.selected),
        candidates=st.ordered_candidates(),
        steps=steps,
        metrics=metrics,
    )


def mapping_table() -> dict[str, str]:
    return dict(LANGGRAPH_MAPPING)


class AgentPlatformState(TypedDict, total=False):
    """Parent graph state: route a request to curation or gallery-chat subgraphs."""

    intent: Literal["curate", "chat"]
    # Curation inputs / outputs (populated when intent=curate).
    candidates: list[Candidate]
    curation_result: Optional[AgentResult]
    # Chat inputs / outputs (populated when intent=chat) — chat subgraph owns details.
    user_text: str
    chat_reply: Optional[str]
    chat_tool_calls: list[dict[str, Any]]
    backend: str


def compile_agent_platform_graph(
    *,
    curation_runner: Callable[[list[Candidate]], AgentResult],
    chat_subgraph: Any,
):
    """Compose curation + gallery-chat as sibling subgraphs under one router.

    ``chat_subgraph`` is the compiled graph from
    :func:`services.agent.conversation_graph.compile_chat_turn_graph`. The platform
    node ``gallery_chat`` invokes that subgraph and maps ``reply`` / ``tool_calls``
    back onto :class:`AgentPlatformState` (explicit invoke keeps parent/child schemas
    decoupled — the interview-visible structure is still a subgraph mount).
    """
    from langgraph.graph import END, START, StateGraph

    def route(state: AgentPlatformState) -> Literal["curation", "gallery_chat"]:
        return "gallery_chat" if state.get("intent") == "chat" else "curation"

    def curation_node(state: AgentPlatformState) -> dict[str, Any]:
        cands = list(state.get("candidates") or [])
        result = curation_runner(cands)
        return {
            "curation_result": result,
            "backend": str((result.metrics or {}).get("backend") or "langgraph"),
        }

    def gallery_chat_node(state: AgentPlatformState) -> dict[str, Any]:
        seed = {
            "user_text": str(state.get("user_text") or ""),
            "tool_calls": [],
            "observations": [],
            "seen_keys": [],
            "rounds_used": 0,
            "max_rounds": 3,
            "pending_call": None,
            "direct_reply": None,
            "force_answer": False,
            "defer_answer": False,
            "reply": None,
            "answer_messages": None,
            "done": False,
            "backend": "langgraph",
        }
        out = chat_subgraph.invoke(seed)
        return {
            "chat_reply": out.get("reply"),
            "chat_tool_calls": list(out.get("tool_calls") or []),
            "backend": str(out.get("backend") or "langgraph"),
        }

    g = StateGraph(AgentPlatformState)
    g.add_node("curation", curation_node)
    g.add_node("gallery_chat", gallery_chat_node)
    g.add_conditional_edges(
        START, route, {"curation": "curation", "gallery_chat": "gallery_chat"}
    )
    g.add_edge("curation", END)
    g.add_edge("gallery_chat", END)
    return g.compile()
