"""StratifiedHeuristicPlanner explores mid-band under a tight analyze budget."""
from __future__ import annotations

import copy

from services.agent.loop import CurationAgent
from services.agent.planner import HeuristicPlanner, StratifiedHeuristicPlanner
from services.agent.tools import AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import ActionType, AgentConfig, Candidate


def _cands(n: int = 20) -> list[Candidate]:
    # Descending fast_score so greedy always picks 00.. first.
    return [
        Candidate(
            image_id=f"{i:02d}",
            image_path=f"/tmp/{i:02d}.jpg",
            features={"fast_score": float(100 - i)},
        )
        for i in range(n)
    ]


def _registry(score_by_id: dict[str, float]) -> ToolRegistry:
    def analyze(image_path: str, tier: str) -> dict:
        cid = image_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return {"score": score_by_id.get(cid, 50.0), "confidence": 0.95, "dimensions": {}, "verdict": tier}

    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze, default_tier="fast"),
        finalize=FinalizeTool(),
    )


def test_stratified_diverges_from_pure_greedy_under_budget():
    cands = _cands(20)
    scores = {f"{i:02d}": 50.0 for i in range(20)}
    cfg = AgentConfig(
        target_keepers=5,
        keep_score_threshold=0.0,
        max_inferences=8,
        max_analyze_candidates=8,
        allow_escalation=False,
        max_steps=80,
    )
    greedy = CurationAgent(
        tools=_registry(scores),
        config=cfg,
        planner=HeuristicPlanner(),
    ).run(copy.deepcopy(cands))
    stratified = CurationAgent(
        tools=_registry(scores),
        config=cfg,
        planner=StratifiedHeuristicPlanner(exploit_ratio=0.5, seed=7),
    ).run(copy.deepcopy(cands))

    greedy_ids = {c.image_id for c in greedy.candidates if c.attempts > 0}
    strat_ids = {c.image_id for c in stratified.candidates if c.attempts > 0}
    assert greedy_ids == {f"{i:02d}" for i in range(8)}
    assert strat_ids != greedy_ids
    assert stratified.steps[-1].call.action == ActionType.FINALIZE


def test_default_agent_uses_stratified_planner():
    agent = CurationAgent(
        tools=_registry({"a": 90.0}),
        config=AgentConfig(target_keepers=1, max_inferences=2, allow_escalation=False),
    )
    assert isinstance(agent._planner, StratifiedHeuristicPlanner)
