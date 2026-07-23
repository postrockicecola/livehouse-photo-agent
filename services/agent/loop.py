"""Curation agent entrypoint: LangGraph-first ReAct runtime.

Production path (default): ``plan → act → reflect`` StateGraph in
:mod:`services.agent.graph`.

Fallback: imperative ``while`` loop when ``LIVEHOUSE_AGENT_RUNTIME=imperative`` or
``langgraph`` is not installed. Behaviour (tools, budgets, step_hook metrics) stays
aligned so job_runner / orchestrator callers do not care which backend ran.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from services.agent.planner import Planner, StratifiedHeuristicPlanner
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
)

logger = logging.getLogger(__name__)

MetricsHook = Callable[[dict[str, Any]], None]
StepHook = Callable[[AgentStep, AgentState], None]


class CurationAgent:
    """Runs agentic curation over candidate photos (LangGraph by default)."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        config: AgentConfig,
        planner: Optional[Planner] = None,
        reflect_fn=default_reflect,
        metrics_hook: Optional[MetricsHook] = None,
        step_hook: Optional[StepHook] = None,
    ) -> None:
        self._tools = tools
        self._config = config
        self._planner = planner or StratifiedHeuristicPlanner()
        self._reflect = reflect_fn
        self._metrics_hook = metrics_hook
        self._step_hook = step_hook

    def _emit_step(self, step: AgentStep, state: AgentState) -> None:
        if self._step_hook is None:
            return
        try:
            self._step_hook(step, state)
        except Exception:
            logger.exception("agent step_hook failed (step #%s)", step.index)

    def run(self, candidates: list[Candidate]) -> AgentResult:
        from services.agent.graph import langgraph_available, run_curation_graph, runtime_preference

        prefer = runtime_preference()
        if prefer == "langgraph" and langgraph_available():
            try:
                return run_curation_graph(
                    candidates,
                    tools=self._tools,
                    config=self._config,
                    planner=self._planner,
                    reflect_fn=self._reflect,
                    step_hook=self._step_hook,
                    metrics_hook=self._metrics_hook,
                )
            except Exception:
                logger.exception("LangGraph curation runtime failed; falling back to imperative loop")

        result = self._run_imperative(candidates)
        result.metrics = {**(result.metrics or {}), "backend": "imperative"}
        return result

    def _run_imperative(self, candidates: list[Candidate]) -> AgentResult:
        """Legacy while-loop runtime (fallback / explicit ``imperative`` preference)."""
        state = AgentState.from_candidates(candidates, self._config)
        steps: list[AgentStep] = []
        fallback_calls = 0

        while True:
            if state.step_index >= self._config.max_steps:
                logger.warning("agent hit max_steps=%s; forcing finalize", self._config.max_steps)
                forced = self._forced_finalize(state, "max_steps_reached")
                steps.append(forced)
                self._emit_step(forced, state)
                break

            call = self._planner.next_action(state)
            result = self._tools.dispatch(state, call)
            state.step_index += 1
            state.inferences_used += result.inference_cost
            if call.source == "llm_fallback":
                fallback_calls += 1

            reflection_note: Optional[str] = None
            if call.action == ActionType.ANALYZE and result.inference_cost > 0:
                cand = state.candidates.get(call.image_id or "")
                if cand is not None:
                    verdict = self._reflect(cand, self._config)
                    if verdict.escalate and not state.budget_exhausted():
                        state.pending_escalations.append(cand.image_id)
                        state.escalations += 1
                        reflection_note = f"queue escalation: {verdict.reason}"
                    elif not verdict.valid:
                        reflection_note = f"invalid output (no retry): {verdict.reason}"

            step = AgentStep(index=state.step_index, call=call, result=result, reflection=reflection_note)
            steps.append(step)
            self._emit_step(step, state)

            if call.action == ActionType.FINALIZE and result.ok:
                break

        metrics = self._build_metrics(state, steps, fallback_calls)
        if self._metrics_hook is not None:
            try:
                self._metrics_hook(metrics)
            except Exception:
                logger.exception("agent metrics_hook failed")

        return AgentResult(
            selected=list(state.selected),
            candidates=state.ordered_candidates(),
            steps=steps,
            metrics=metrics,
        )

    def _forced_finalize(self, state: AgentState, why: str) -> AgentStep:
        call = ToolCall(action=ActionType.FINALIZE, reason=why, source="loop_guard")
        result = self._tools.dispatch(state, call)
        state.step_index += 1
        return AgentStep(index=state.step_index, call=call, result=result)

    def _build_metrics(self, state: AgentState, steps: list[AgentStep], fallback_calls: int) -> dict[str, Any]:
        from services.agent.graph import build_metrics

        return build_metrics(state, steps, fallback_calls, backend="imperative")
