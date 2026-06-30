"""The curation agent loop: observe → plan → act → reflect, under a budget.

This ties the pieces together into a real ReAct-style controller:

    while not done:
        call   = planner.next_action(state)     # decide (LLM or heuristic)
        result = tools.dispatch(state, call)    # act (inspect / analyze / finalize)
        reflect(...)                            # self-correct: queue escalations
        record AgentStep                        # structured trace for observability

Termination is guaranteed by three guards — explicit FINALIZE, the step ceiling,
and the inference budget — so the loop can never run away. Every step is recorded,
and the run returns a metrics dict (steps, inferences, escalations, fallbacks,
selection size) that doubles as the observability surface for A's cost narrative.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

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
)

logger = logging.getLogger(__name__)

MetricsHook = Callable[[dict[str, Any]], None]
# Called after every recorded step; the job runner uses it to stream agent
# decisions into ``job_events`` so the Infra Console timeline shows the loop live.
StepHook = Callable[[AgentStep, AgentState], None]


class CurationAgent:
    """Runs the agentic curation loop over a set of candidate photos."""

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
        self._planner = planner or HeuristicPlanner()
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
        action_counts: dict[str, int] = {}
        # Who actually decided each step: "llm" (model in control), "llm_fallback"
        # (model output was unusable → heuristic rescued it), "heuristic", "reflection",
        # or "loop_guard". This is the structured-output-reliability number for the
        # LLM-agent narrative: how often the model drove vs how often we fell back.
        source_counts: dict[str, int] = {}
        for s in steps:
            action_counts[s.call.action.value] = action_counts.get(s.call.action.value, 0) + 1
            source_counts[s.call.source] = source_counts.get(s.call.source, 0) + 1
        llm_steps = source_counts.get("llm", 0)
        llm_total = llm_steps + fallback_calls
        return {
            "steps": len(steps),
            "inferences_used": state.inferences_used,
            "max_inferences": self._config.max_inferences,
            "budget_exhausted": state.budget_exhausted(),
            "escalations": state.escalations,
            "llm_fallback_calls": fallback_calls,
            # Fraction of LLM-attempted steps the model drove without falling back.
            "llm_decision_rate": (llm_steps / llm_total) if llm_total else None,
            "candidates_total": len(state.candidates),
            "candidates_analyzed": state.analyzed_count(),
            "selected_count": len(state.selected),
            "action_counts": action_counts,
            "planner_source_counts": source_counts,
        }
