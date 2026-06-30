"""Tests for agent-to-agent handoff over a message bus (messaging + HandoffCoordinator).

Covered behaviors:
- MessageBus: send assigns seq, drain is atomic + one-shot, history is append-only;
- build_handoff_messages flags only the candidates an agent analyzed but is unsure about;
- HandoffCoordinator: workers triage at fast tier, hand off low-confidence candidates to
  a specialist that re-analyzes them at the FULL tier, and the bus carries the messages;
- the merge prefers the specialist's full-tier verdict for handed-off candidates;
- a run with no ambiguous candidates spawns no specialist and sends no messages.

Deterministic fake analysis backend — no model server / GPU.
"""
from __future__ import annotations

from services.agent.messaging import (
    ROLE_SPECIALIST,
    AgentMessage,
    MessageBus,
    build_handoff_messages,
)
from services.agent.orchestrator import (
    HandoffCoordinator,
    ShardRouter,
    default_subagent_factory,
)
from services.agent.tools import AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import AgentConfig, Candidate
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _full_dims(value: int = 7) -> dict[str, int]:
    return {k: value for k in STAGE3_DIM_KEYS}


def _make_candidates(specs: list[tuple[str, float]]) -> list[Candidate]:
    return [
        Candidate(image_id=cid, image_path=f"/tmp/{cid}.jpg", features={"fast_score": fs})
        for cid, fs in specs
    ]


def _registry(analyze_fn) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )


# --------------------------------------------------------------------- MessageBus


def test_message_bus_send_drain_history():
    bus = MessageBus()
    bus.send(AgentMessage(sender="w0", recipient=ROLE_SPECIALIST, kind="handoff", payload={"image_id": "a"}))
    bus.send(AgentMessage(sender="w1", recipient=ROLE_SPECIALIST, kind="handoff", payload={"image_id": "b"}))

    assert bus.pending(ROLE_SPECIALIST) == 2
    drained = bus.drain(ROLE_SPECIALIST)
    assert [m.seq for m in drained] == [1, 2]  # seq assigned in send order
    assert bus.drain(ROLE_SPECIALIST) == []  # one-shot: second drain is empty
    assert len(bus.history()) == 2  # history is append-only, unaffected by drain


# ----------------------------------------------------------- build_handoff_messages


def test_build_handoff_flags_only_low_confidence_analyzed():
    # a: analyzed, confident -> keep (no handoff); b: analyzed, low conf -> handoff;
    # c: never analyzed (attempts 0) -> not a handoff.
    a = Candidate("a", "/tmp/a.jpg", features={"fast_score": 9})
    a.attempts, a.tier, a.score, a.confidence = 1, "fast", 90.0, 0.95
    a.analysis = {"score": 90.0, "confidence": 0.95, "dimensions": _full_dims()}
    b = Candidate("b", "/tmp/b.jpg", features={"fast_score": 8})
    b.attempts, b.tier, b.score, b.confidence = 1, "fast", 72.0, 0.4
    b.analysis = {"score": 72.0, "confidence": 0.4, "dimensions": _full_dims()}
    c = Candidate("c", "/tmp/c.jpg", features={"fast_score": 5})

    class _R:
        candidates = [a, b, c]

    cfg = AgentConfig(confidence_floor=0.8, base_tier="fast", escalation_tier="full")
    msgs = build_handoff_messages(_R(), cfg, sender="w0")
    assert [m.payload["image_id"] for m in msgs] == ["b"]
    assert msgs[0].kind == "handoff" and msgs[0].recipient == ROLE_SPECIALIST


# --------------------------------------------------------------- HandoffCoordinator


def _tiered_analyze(fast_conf: float = 0.4, fast_score: float = 72.0):
    """fast tier = shaky (low confidence) so it gets handed off; full tier = confident."""

    def _fn(image_path: str, tier: str) -> dict:
        cid = image_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if tier == "full":
            return {"score": 95.0, "confidence": 0.99, "dimensions": _full_dims(), "verdict": f"full {cid}"}
        return {"score": fast_score, "confidence": fast_conf, "dimensions": _full_dims(), "verdict": f"fast {cid}"}

    return _fn


def test_handoff_coordinator_routes_low_conf_to_specialist():
    cands = _make_candidates([("a", 9.0), ("b", 8.0), ("c", 7.0), ("d", 6.0)])
    cfg = AgentConfig(
        max_inferences=20,
        target_keepers=4,
        keep_score_threshold=70.0,
        confidence_floor=0.8,
    )
    coord = HandoffCoordinator(
        subagent_factory=default_subagent_factory(tools=_registry(_tiered_analyze(fast_conf=0.4))),
        config=cfg,
        router=ShardRouter(num_shards=2),
        specialist_fraction=0.4,
    )
    res = coord.run(cands)

    m = res.metrics
    assert m["handoff"] is True
    assert m["specialist_ran"] is True
    # every fast analysis was low-confidence -> all 4 handed off to the specialist
    assert m["handoffs"] == 4
    assert m["specialist_analyzed"] == 4
    assert m["specialist_inferences"] >= 1
    # bus carried one message per handoff
    assert len(coord.bus.history()) == 4
    # final keepers carry the specialist's full-tier score (95), not the fast 72
    by_id = {c.image_id: c for c in res.candidates}
    assert all(by_id[cid].tier == "full" and by_id[cid].score == 95.0 for cid in res.selected)


def test_handoff_no_ambiguous_means_no_specialist():
    # High fast-tier confidence -> nothing to hand off -> no specialist, no messages.
    cands = _make_candidates([("a", 9.0), ("b", 8.0)])
    cfg = AgentConfig(max_inferences=12, target_keepers=2, keep_score_threshold=70.0, confidence_floor=0.8)
    coord = HandoffCoordinator(
        subagent_factory=default_subagent_factory(
            tools=_registry(_tiered_analyze(fast_conf=0.99, fast_score=90.0))
        ),
        config=cfg,
        router=ShardRouter(num_shards=2),
    )
    res = coord.run(cands)

    assert res.metrics["handoffs"] == 0
    assert res.metrics["specialist_ran"] is False
    assert len(coord.bus.history()) == 0
    # workers' confident fast-tier keepers stand as the final selection
    assert set(res.selected) == {"a", "b"}


def test_handoff_budget_is_partitioned():
    cands = _make_candidates([(f"img{i}", float(10 - i)) for i in range(6)])
    cfg = AgentConfig(max_inferences=10, target_keepers=6, keep_score_threshold=70.0, confidence_floor=0.8)
    coord = HandoffCoordinator(
        subagent_factory=default_subagent_factory(tools=_registry(_tiered_analyze(fast_conf=0.4))),
        config=cfg,
        router=ShardRouter(num_shards=2),
        specialist_fraction=0.3,
    )
    res = coord.run(cands)
    # worker tier + specialist tier together never exceed the global budget
    assert res.metrics["inferences_used"] <= cfg.max_inferences
    assert res.metrics["worker_inferences"] >= 1
