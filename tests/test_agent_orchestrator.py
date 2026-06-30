"""Tests for multi-agent orchestration (services/agent/orchestrator.py).

Covered behaviors:
- ShardRouter splits candidates into disjoint shards and the budget is propagated
  (sum of child budgets == parent, no shard starved);
- the coordinator fans out to one sub-agent per shard, then merges + globally re-ranks
  the selection and caps it to the parent target_keepers;
- aggregate metrics roll up sub-agent metrics (steps/inferences/fallbacks) and recompute
  llm_decision_rate;
- the orchestrator step_hook attributes every step to its agent_id;
- KeyedRouter groups by a key function;
- concurrent fan-out (max_workers > 1) is deterministic (same result as sequential).

All backends are deterministic fakes — no model server / GPU.
"""
from __future__ import annotations

from services.agent.orchestrator import (
    Coordinator,
    KeyedRouter,
    ShardRouter,
    default_subagent_factory,
    split_budget,
)
from services.agent.tools import AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import ActionType, AgentConfig, Candidate
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _full_dims(value: int = 7) -> dict[str, int]:
    return {k: value for k in STAGE3_DIM_KEYS}


def _make_candidates(specs: list[tuple[str, float]]) -> list[Candidate]:
    return [
        Candidate(
            image_id=cid,
            image_path=f"/tmp/{cid}.jpg",
            features={"fast_score": fs, "tech_score": fs - 1, "blur_type": None},
        )
        for cid, fs in specs
    ]


def _registry(analyze_fn) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )


def _fake_analyze(score_by_id: dict[str, float], *, confidence: float = 0.95):
    def _fn(image_path: str, tier: str) -> dict:
        cid = image_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return {
            "score": score_by_id.get(cid, 50.0),
            "confidence": confidence,
            "dimensions": _full_dims(),
            "verdict": f"{tier} for {cid}",
        }

    return _fn


# --------------------------------------------------------------------- budget split


def test_split_budget_partitions_and_never_starves():
    cfg = AgentConfig(max_inferences=10, max_analyze_candidates=30)
    parts = split_budget(cfg, 3)
    assert [p.max_inferences for p in parts] == [4, 3, 3]  # sums to 10
    assert sum(p.max_inferences for p in parts) == 10
    assert sum(p.max_analyze_candidates for p in parts) == 30
    assert all(p.max_inferences >= 1 for p in parts)


def test_split_budget_weighted():
    cfg = AgentConfig(max_inferences=12, max_analyze_candidates=12)
    parts = split_budget(cfg, 2, weights=[3, 1])
    assert sum(p.max_inferences for p in parts) == 12
    assert parts[0].max_inferences > parts[1].max_inferences


# --------------------------------------------------------------------- shard routing


def test_shard_router_contiguous_disjoint_and_budget_propagated():
    cands = _make_candidates([(f"img{i}", float(10 - i)) for i in range(6)])
    cfg = AgentConfig(max_inferences=9, target_keepers=10, allow_escalation=False)
    tasks = ShardRouter(num_shards=3, strategy="contiguous").route(cands, cfg)

    assert len(tasks) == 3
    # disjoint, covers everything, order preserved within a contiguous shard
    seen = [c.image_id for t in tasks for c in t.candidates]
    assert seen == [c.image_id for c in cands]
    assert sum(t.config.max_inferences for t in tasks) == 9


def test_shard_router_drops_empty_shards():
    cands = _make_candidates([("a", 1.0)])
    tasks = ShardRouter(num_shards=4).route(cands, AgentConfig(max_inferences=8))
    assert len(tasks) == 1  # only one non-empty shard despite asking for 4


# --------------------------------------------------------------------- coordinator run


def test_coordinator_merges_and_caps_global_keepers():
    # Two shards, each would keep its own top scorers; the global cap (target_keepers=2)
    # collapses the union to the 2 best across BOTH shards, score-ordered.
    cands = _make_candidates([("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0)])
    analyze = _fake_analyze({"a": 95.0, "b": 75.0, "c": 90.0, "d": 72.0})
    cfg = AgentConfig(max_inferences=10, target_keepers=2, keep_score_threshold=70.0, allow_escalation=False)

    coord = Coordinator(
        subagent_factory=default_subagent_factory(tools=_registry(analyze)),
        config=cfg,
        router=ShardRouter(num_shards=2, strategy="contiguous"),
    )
    res = coord.run(cands)

    # shard0 = {a,b}, shard1 = {c,d}; global top-2 by score = a(95), c(90)
    assert res.selected == ["a", "c"]
    assert res.metrics["num_subagents"] == 2
    assert res.metrics["selected_count"] == 2
    assert res.metrics["candidates_total"] == 4
    # budget never exceeds parent
    assert res.metrics["inferences_used"] <= cfg.max_inferences


def test_coordinator_aggregates_subagent_metrics():
    cands = _make_candidates([(f"img{i}", float(10 - i)) for i in range(6)])
    analyze = _fake_analyze({f"img{i}": 80.0 for i in range(6)})
    cfg = AgentConfig(max_inferences=6, target_keepers=10, keep_score_threshold=70.0, allow_escalation=False)

    coord = Coordinator(
        subagent_factory=default_subagent_factory(tools=_registry(analyze)),
        config=cfg,
        router=ShardRouter(num_shards=3),
    )
    res = coord.run(cands)

    # steps/inferences are the sum of the per-shard runs
    sub_steps = sum(s.result.metrics["steps"] for s in res.subagents)
    assert res.metrics["steps"] == sub_steps
    sub_inf = sum(s.result.metrics["inferences_used"] for s in res.subagents)
    assert res.metrics["inferences_used"] == sub_inf
    assert len(res.metrics["subagents"]) == 3
    # no LLM planner here → decision rate is None (no llm-attempted steps)
    assert res.metrics["llm_decision_rate"] is None


def test_coordinator_step_hook_attributes_agent_id():
    cands = _make_candidates([("a", 9.0), ("b", 8.0)])
    analyze = _fake_analyze({"a": 90.0, "b": 85.0})
    cfg = AgentConfig(max_inferences=6, target_keepers=5, keep_score_threshold=70.0, allow_escalation=False)

    seen: list[tuple[str, ActionType]] = []

    def hook(agent_id, step, state):
        seen.append((agent_id, step.call.action))

    coord = Coordinator(
        subagent_factory=default_subagent_factory(tools=_registry(analyze)),
        config=cfg,
        router=ShardRouter(num_shards=2),
        step_hook=hook,
    )
    coord.run(cands)

    agent_ids = {aid for aid, _ in seen}
    assert agent_ids == {"sub-0", "sub-1"}
    assert any(action == ActionType.FINALIZE for _, action in seen)


def test_keyed_router_groups_by_key():
    cands = _make_candidates([("set1-a", 9.0), ("set2-a", 8.0), ("set1-b", 7.0)])
    analyze = _fake_analyze({"set1-a": 90.0, "set2-a": 88.0, "set1-b": 80.0})
    cfg = AgentConfig(max_inferences=6, target_keepers=10, keep_score_threshold=70.0, allow_escalation=False)

    coord = Coordinator(
        subagent_factory=default_subagent_factory(tools=_registry(analyze)),
        config=cfg,
        router=KeyedRouter(lambda c: c.image_id.split("-", 1)[0]),
    )
    res = coord.run(cands)

    assert res.metrics["num_subagents"] == 2  # set1, set2
    labels = {s.agent_id for s in res.subagents}
    assert labels == {"key:set1", "key:set2"}


def test_concurrent_fanout_matches_sequential():
    analyze = _fake_analyze({f"img{i}": float(72 + i) for i in range(8)})
    cfg = AgentConfig(max_inferences=12, target_keepers=3, keep_score_threshold=70.0, allow_escalation=False)

    def _run(workers: int):
        # Fresh candidate objects per run: the loop mutates them in place (inspected/
        # analysis), so reusing the same list across runs would skip work the 2nd time.
        cands = _make_candidates([(f"img{i}", float(20 - i)) for i in range(8)])
        coord = Coordinator(
            subagent_factory=default_subagent_factory(tools=_registry(analyze)),
            config=cfg,
            router=ShardRouter(num_shards=4),
            max_workers=workers,
        )
        return coord.run(cands)

    seq = _run(1)
    par = _run(4)
    assert seq.selected == par.selected
    assert seq.metrics["inferences_used"] == par.metrics["inferences_used"]
