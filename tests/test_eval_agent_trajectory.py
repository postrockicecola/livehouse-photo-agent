"""Offline tests for the label-free agent trajectory eval harness.

No model / labels: candidates and analyze are the harness's own deterministic fakes.
The LLM arm is a *scripted* :class:`LLMPlanner` whose completion always emits a valid,
in-contract JSON action (analyze the first un-analyzed candidate; finalize once the
budget is spent), so the harness's ``llm_decision_rate`` / ``fallback_rate`` accounting
can be asserted without a real model.
"""
from __future__ import annotations

import json
import re

from scripts.eval.eval_agent_trajectory import (
    evaluate,
    synthetic_analyze_fn,
    synthetic_candidates,
)
from services.agent.planner import HeuristicPlanner, LLMPlanner
from services.agent.types import AgentConfig


def _scripted_complete(prompt: str) -> str:
    """A perfectly-behaved planner LLM: analyze un-analyzed candidates, finalize on budget."""
    budget = json.loads(re.search(r"BUDGET: (\{.*\})", prompt).group(1))
    if budget["inferences_used"] >= budget["max_inferences"]:
        return json.dumps({"action": "finalize", "reason": "budget spent"})
    rows = json.loads(re.search(r"CANDIDATES: (\[.*\])", prompt).group(1))
    for r in rows:
        if not r.get("analyzed"):
            return json.dumps({"action": "analyze", "idx": r["idx"], "tier": "fast", "reason": "scripted"})
    return json.dumps({"action": "finalize", "reason": "done"})


def _cfg(budget: int) -> AgentConfig:
    return AgentConfig(
        target_keepers=5,
        keep_score_threshold=70.0,
        max_inferences=budget,
        max_analyze_candidates=budget,
        allow_escalation=False,  # keep the trajectory deterministic for assertions
        max_steps=200,
    )


def test_trajectory_eval_reports_reliability_for_llm_arm():
    budget = 5
    candidates = synthetic_candidates(12, seed=7)
    analyze_fn = synthetic_analyze_fn(seed=7)
    arms = {
        "heuristic": HeuristicPlanner(),
        "llm": LLMPlanner(_scripted_complete, fallback=HeuristicPlanner()),
    }

    report = evaluate(arms, candidates, analyze_fn, _cfg(budget), repeats=1, baseline="heuristic")

    assert report["candidates_total"] == 12
    assert report["budget"] == budget
    assert set(report["arms"]) == {"heuristic", "llm"}

    heur = report["arms"]["heuristic"]["mean"]
    llm = report["arms"]["llm"]["mean"]

    # Both spend the whole budget (budget < candidates).
    assert heur["budget_utilization"] == 1.0
    assert llm["budget_utilization"] == 1.0

    # The scripted model drove every decision it was asked for, with zero fallbacks.
    assert llm["llm_decision_rate"] == 1.0
    assert llm["fallback_rate"] == 0.0
    assert report["arms"]["llm"]["mean"]["llm_fallback_calls"] == 0.0

    # A purely-heuristic run has no LLM decisions to rate.
    assert heur["llm_decision_rate"] is None
    assert heur["fallback_rate"] is None

    # Non-baseline arms carry a head-to-head delta.
    assert "delta_vs_heuristic" in report["arms"]["llm"]
    assert "delta_vs_heuristic" not in report["arms"]["heuristic"]


def test_trajectory_eval_aggregates_repeats_with_std():
    candidates = synthetic_candidates(20, seed=1)
    analyze_fn = synthetic_analyze_fn(seed=1)
    arms = {"heuristic": HeuristicPlanner()}

    report = evaluate(arms, candidates, analyze_fn, _cfg(6), repeats=3, baseline="heuristic")
    arm = report["arms"]["heuristic"]

    assert arm["repeats"] == 3
    assert len(arm["runs"]) == 3
    # Deterministic arm → zero variance across repeats.
    assert arm["std"]["inferences_used"] == 0.0
    assert arm["mean"]["inferences_used"] == 6.0
