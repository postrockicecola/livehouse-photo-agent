"""Smoke tests for the agent-selection eval harness (scripts/eval/eval_agent_selection).

Verifies the planner-allocation eval runs end-to-end on a tiny synthetic set and that
the oracle allocator (which analyzes by true human score) recovers human keepers at
least as well as random under a constrained budget. No model / network needed.
"""
from __future__ import annotations

from scripts.eval.eval_agent_selection import (
    OracleAllocationPlanner,
    RandomAllocationPlanner,
    build_candidates,
    make_oracle_analyze_fn,
    run_arm,
    score_arm,
)
from services.agent.planner import HeuristicPlanner
from services.agent.types import AgentConfig


def _setup():
    # 10 images; "good*" are human keepers with high overall, "bad*" are not.
    files = [f"good{i}.jpg" for i in range(4)] + [f"bad{i}.jpg" for i in range(6)]
    truth = {f: {"overall": 90.0 if f.startswith("good") else 20.0, "keep": f.startswith("good")} for f in files}
    oracle_scores = {f: truth[f]["overall"] for f in files}
    # fast_score is deliberately anti-correlated with keep (bad images look clean).
    fast = {f: (10.0 if f.startswith("bad") else 1.0) for f in files}
    return files, truth, oracle_scores, fast


def test_eval_runs_and_oracle_beats_random_on_keeper_recall():
    files, truth, oracle_scores, fast = _setup()
    candidates = build_candidates(files, fast)
    analyze_fn = make_oracle_analyze_fn(oracle_scores)
    cfg = dict(
        target_keepers=4, keep_score_threshold=0.0, max_inferences=4,
        max_analyze_candidates=4, allow_escalation=False, max_steps=100,
    )

    oracle = score_arm(run_arm(OracleAllocationPlanner(truth), candidates, analyze_fn, AgentConfig(**cfg)), truth, [4])
    rand = score_arm(run_arm(RandomAllocationPlanner(seed=1), candidates, analyze_fn, AgentConfig(**cfg)), truth, [4])

    # Oracle spends its 4-analyze budget exactly on the 4 keepers.
    assert oracle["analyzed_keeper_recall"] == 1.0
    assert oracle["analyzed_keeper_recall"] >= rand["analyzed_keeper_recall"]
    assert oracle["precision_recall_at_k"]["4"]["precision"] == 1.0


def test_heuristic_fast_score_allocation_can_underperform():
    # When fast_score is anti-correlated with keep, greedy-by-fast_score wastes budget.
    files, truth, oracle_scores, fast = _setup()
    candidates = build_candidates(files, fast)
    analyze_fn = make_oracle_analyze_fn(oracle_scores)
    cfg = AgentConfig(
        target_keepers=4, keep_score_threshold=0.0, max_inferences=4,
        max_analyze_candidates=4, allow_escalation=False, max_steps=100,
    )
    heur = score_arm(run_arm(HeuristicPlanner(), candidates, analyze_fn, cfg), truth, [4])
    # Greedy fast_score analyzes the "clean but not kept" frames first → 0 keepers found.
    assert heur["analyzed_keeper_recall"] == 0.0
