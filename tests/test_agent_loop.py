"""Tests for the curation agent loop (services/agent).

Covered behaviors:
- end-to-end heuristic run: inspect → analyze → finalize, correct selection;
- inference budget caps the number of VLM calls;
- reflection escalates low-confidence / ambiguous results to the full tier once;
- LLM tool-calling drives actions, and malformed LLM output falls back safely;
- the max_steps guard always terminates with a finalize.

The analysis backend is a deterministic fake, so no model server / GPU is needed.
"""
from __future__ import annotations

import json

from services.agent.loop import CurationAgent
from services.agent.planner import HeuristicPlanner, LLMPlanner
from services.agent.tools import (
    AnalyzeTool,
    FinalizeTool,
    InspectTool,
    QueryGalleryTool,
    ToolRegistry,
)
from services.agent.types import ActionType, AgentConfig, Candidate
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _full_dims(value: int = 7) -> dict[str, int]:
    return {k: value for k in STAGE3_DIM_KEYS}


def _make_candidates(specs: list[tuple[str, float]]) -> list[Candidate]:
    """specs: list of (image_id, fast_score)."""
    return [
        Candidate(
            image_id=cid,
            image_path=f"/tmp/{cid}.jpg",
            features={"fast_score": fs, "tech_score": fs - 1, "blur_type": None},
        )
        for cid, fs in specs
    ]


def _registry(analyze_fn, *, feature_provider=None) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(feature_provider),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )


def _fake_analyze(score_by_id: dict[str, float], *, confidence: float = 0.95):
    """Build an analyze_fn that scores by the image basename, full rubric, high conf."""

    def _fn(image_path: str, tier: str) -> dict:
        cid = image_path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        return {
            "score": score_by_id.get(cid, 50.0),
            "confidence": confidence,
            "dimensions": _full_dims(),
            "verdict": f"{tier} verdict for {cid}",
        }

    return _fn


def test_heuristic_end_to_end_selects_top_scorers():
    cands = _make_candidates([("a", 9.0), ("b", 8.0), ("c", 2.0)])
    analyze = _fake_analyze({"a": 90.0, "b": 80.0, "c": 40.0})
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(target_keepers=2, keep_score_threshold=70.0, allow_escalation=False),
    )
    res = agent.run(cands)

    assert res.steps[-1].call.action == ActionType.FINALIZE
    assert res.selected == ["a", "b"]  # best-first, c below threshold
    assert res.metrics["candidates_analyzed"] == 3
    assert res.metrics["selected_count"] == 2
    # every candidate inspected before analysis
    assert all(c.inspected for c in res.candidates)


def test_inference_budget_caps_analysis():
    cands = _make_candidates([(f"img{i}", float(10 - i)) for i in range(8)])
    analyze = _fake_analyze({f"img{i}": 85.0 for i in range(8)})
    # Pin greedy heuristic: default StratifiedHeuristicPlanner intentionally explores.
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(max_inferences=3, allow_escalation=False, target_keepers=10),
        planner=HeuristicPlanner(),
    )
    res = agent.run(cands)

    assert res.metrics["inferences_used"] == 3
    assert res.metrics["budget_exhausted"] is True
    # highest fast_score images are the ones that got analyzed
    analyzed_ids = {c.image_id for c in res.candidates if c.attempts > 0}
    assert analyzed_ids == {"img0", "img1", "img2"}


def test_reflection_escalates_low_confidence_once():
    cands = _make_candidates([("a", 9.0)])

    calls: list[str] = []

    def analyze(image_path: str, tier: str) -> dict:
        calls.append(tier)
        # fast tier returns low confidence -> should escalate; full tier confident
        conf = 0.5 if tier == "fast" else 0.95
        return {
            "score": 72.0,
            "confidence": conf,
            "dimensions": _full_dims(),
            "verdict": f"{tier}",
        }

    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(
            confidence_floor=0.8,
            allow_escalation=True,
            base_tier="fast",
            escalation_tier="full",
            target_keepers=1,
        ),
    )
    res = agent.run(cands)

    assert calls == ["fast", "full"]  # escalated exactly once
    assert res.metrics["escalations"] == 1
    cand = res.candidates[0]
    assert cand.escalated is True
    assert cand.tier == "full"
    assert cand.confidence == 0.95


def test_reflection_escalates_ambiguous_band_score():
    cands = _make_candidates([("a", 9.0)])
    tiers: list[str] = []

    def analyze(image_path: str, tier: str) -> dict:
        tiers.append(tier)
        return {"score": 70.0, "confidence": 0.99, "dimensions": _full_dims(), "verdict": tier}

    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(ambiguous_band=(65.0, 75.0), confidence_floor=0.0, target_keepers=1),
    )
    res = agent.run(cands)
    assert tiers == ["fast", "full"]
    assert res.metrics["escalations"] == 1


def test_no_escalation_when_disabled():
    cands = _make_candidates([("a", 9.0)])
    tiers: list[str] = []

    def analyze(image_path: str, tier: str) -> dict:
        tiers.append(tier)
        return {"score": 70.0, "confidence": 0.1, "dimensions": _full_dims(), "verdict": tier}

    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=1),
    )
    agent.run(cands)
    assert tiers == ["fast"]


def test_llm_planner_drives_actions():
    cands = _make_candidates([("a", 9.0), ("b", 1.0)])
    analyze = _fake_analyze({"a": 88.0, "b": 30.0})

    # Scripted LLM: inspect a, analyze a (full), then finalize just [a].
    script = iter(
        [
            json.dumps({"action": "inspect", "image_id": "a", "reason": "look at a"}),
            json.dumps({"action": "analyze", "image_id": "a", "tier": "full", "reason": "deep a"}),
            json.dumps({"action": "finalize", "selected": ["a"], "reason": "done"}),
        ]
    )

    def complete(_prompt: str) -> str:
        return next(script)

    # auto_inspect=False: exercise the LLM driving *every* step, including inspect.
    planner = LLMPlanner(complete, auto_inspect=False)
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=5),
        planner=planner,
    )
    res = agent.run(cands)

    assert res.selected == ["a"]
    assert [s.call.action for s in res.steps] == [
        ActionType.INSPECT,
        ActionType.ANALYZE,
        ActionType.FINALIZE,
    ]
    assert all(s.call.source == "llm" for s in res.steps)
    assert res.candidates[0].tier == "full"


def test_llm_planner_drives_actions_by_index():
    # Same as above, but the model references photos by integer idx (the robust path
    # for small models) instead of echoing long filenames.
    cands = _make_candidates([("a", 9.0), ("b", 1.0)])
    analyze = _fake_analyze({"a": 88.0, "b": 30.0})

    script = iter(
        [
            json.dumps({"action": "inspect", "idx": 0, "reason": "look at first"}),
            json.dumps({"action": "analyze", "idx": 0, "tier": "full", "reason": "deep first"}),
            json.dumps({"action": "finalize", "selected": [0], "reason": "done"}),
        ]
    )

    planner = LLMPlanner(lambda _p: next(script), auto_inspect=False)
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=5),
        planner=planner,
    )
    res = agent.run(cands)

    assert res.selected == ["a"]
    assert all(s.call.source == "llm" for s in res.steps)
    assert res.candidates[0].tier == "full"
    assert res.metrics["llm_decision_rate"] == 1.0


def test_llm_planner_auto_inspect_only_consults_llm_for_analyze_finalize():
    # With auto_inspect (default), the loop inspects mechanically; the LLM is asked
    # only for analyze/finalize. The model here picks the best, then finalizes.
    cands = _make_candidates([("a", 9.0), ("b", 3.0)])
    analyze = _fake_analyze({"a": 90.0, "b": 40.0})

    script = iter(
        [
            json.dumps({"action": "analyze", "idx": 0, "reason": "best fast_score"}),
            json.dumps({"action": "finalize", "selected": [0], "reason": "one keeper is enough"}),
        ]
    )

    planner = LLMPlanner(lambda _p: next(script))  # auto_inspect defaults True
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=5, keep_score_threshold=70.0),
        planner=planner,
    )
    res = agent.run(cands)

    # Inspects are mechanical (no LLM); analyze + finalize are the LLM's calls.
    assert [s.call.source for s in res.steps] == ["auto_inspect", "auto_inspect", "llm", "llm"]
    assert res.selected == ["a"]
    # decision_rate counts only LLM-attempted steps; both succeeded → 1.0
    assert res.metrics["llm_decision_rate"] == 1.0
    assert res.metrics["planner_source_counts"]["auto_inspect"] == 2


def test_llm_planner_progress_guard_breaks_inspect_loop():
    # A degenerate model that always re-inspects idx 0 must not livelock: the progress
    # guard rejects the no-op, the heuristic fallback advances, and the run terminates.
    cands = _make_candidates([("a", 9.0), ("b", 8.0)])
    analyze = _fake_analyze({"a": 90.0, "b": 80.0})

    def complete(_p: str) -> str:
        return json.dumps({"action": "inspect", "idx": 0, "reason": "again"})

    planner = LLMPlanner(complete, fallback=HeuristicPlanner())
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(max_steps=50, target_keepers=2, keep_score_threshold=70.0, allow_escalation=False),
        planner=planner,
    )
    res = agent.run(cands)

    assert res.steps[-1].call.action == ActionType.FINALIZE
    assert res.selected == ["a", "b"]  # fallback still made progress
    assert res.metrics["llm_fallback_calls"] >= 1


def test_llm_planner_falls_back_on_garbage():
    cands = _make_candidates([("a", 9.0)])
    analyze = _fake_analyze({"a": 90.0})

    def complete(_prompt: str) -> str:
        return "I think we should look at the nice photos!"  # not JSON

    planner = LLMPlanner(complete, fallback=HeuristicPlanner(), auto_inspect=False)
    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=1),
        planner=planner,
    )
    res = agent.run(cands)

    assert res.metrics["llm_fallback_calls"] >= 1
    assert all(s.call.source == "llm_fallback" for s in res.steps)
    assert res.selected == ["a"]


def test_max_steps_guard_forces_finalize():
    cands = _make_candidates([("a", 9.0)])

    class _StuckPlanner:
        def next_action(self, state):
            # never finalizes, never makes progress
            from services.agent.types import ToolCall

            return ToolCall(action=ActionType.INSPECT, image_id="a", reason="loop forever")

    agent = CurationAgent(
        tools=_registry(_fake_analyze({"a": 90.0})),
        config=AgentConfig(max_steps=5),
        planner=_StuckPlanner(),
    )
    res = agent.run(cands)

    assert res.steps[-1].call.action == ActionType.FINALIZE
    assert res.steps[-1].call.source == "loop_guard"
    assert res.metrics["steps"] <= 6  # 5 stuck steps + 1 forced finalize


def test_llm_planner_uses_cluster_recall_compare_tools():
    # A capable model uses the zero-cost tools: cluster the bursts, recall one score
    # from the gallery (no inference), compare two candidates, analyze the rest, finalize.
    cands = _make_candidates(
        [("a_0001.jpg", 8.0), ("a_0002.jpg", 7.0), ("b_0500.jpg", 6.0), ("c_0900.jpg", 5.0)]
    )
    analyze = _fake_analyze({"a_0001.jpg": 82.0, "b_0500.jpg": 78.0})

    def gallery(image_id: str):
        return {"score": 85.0, "confidence": 0.9} if image_id == "c_0900.jpg" else None

    tools = ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze, default_tier="fast"),
        finalize=FinalizeTool(),
        query_gallery=QueryGalleryTool(gallery),
    )

    # auto_inspect handles the 4 inspects; the LLM supplies the decisions below.
    script = iter(
        [
            json.dumps({"action": "cluster", "reason": "group bursts"}),
            json.dumps({"action": "query_gallery", "idx": 3, "reason": "recall c_0900"}),
            json.dumps({"action": "analyze", "idx": 0, "tier": "fast", "reason": "burst rep"}),
            json.dumps({"action": "compare", "a": 0, "b": 2, "reason": "weigh close pair"}),
            json.dumps({"action": "analyze", "idx": 2, "tier": "fast", "reason": "the other keeper"}),
            json.dumps({"action": "finalize", "selected": [0, 2, 3], "reason": "commit"}),
        ]
    )

    agent = CurationAgent(
        tools=tools,
        config=AgentConfig(allow_escalation=False, target_keepers=5, keep_score_threshold=70.0),
        planner=LLMPlanner(lambda _p: next(script)),
    )
    res = agent.run(cands)

    counts = res.metrics["action_counts"]
    assert counts["cluster"] == 1
    assert counts["query_gallery"] == 1
    assert counts["compare"] == 1
    assert counts["analyze"] == 2
    assert counts["finalize"] == 1

    # The recalled photo is a keeper with zero inference spent on it.
    recalled = next(c for c in res.candidates if c.image_id == "c_0900.jpg")
    assert recalled.tier == "recall"
    assert recalled.attempts == 0
    assert res.metrics["inferences_used"] == 2  # only the two analyze calls cost budget
    assert res.selected == ["a_0001.jpg", "b_0500.jpg", "c_0900.jpg"]
    # Every non-inspect decision was the model's.
    assert res.metrics["planner_source_counts"].get("llm") == 6


def test_analyze_tool_handles_backend_error():
    cands = _make_candidates([("a", 9.0)])

    def analyze(image_path: str, tier: str) -> dict:
        return {"error": True, "reason": "model timeout"}

    agent = CurationAgent(
        tools=_registry(analyze),
        config=AgentConfig(allow_escalation=False, target_keepers=1),
    )
    res = agent.run(cands)

    cand = res.candidates[0]
    assert cand.analyzed is False
    assert res.selected == []  # errored analysis is not a keeper
    assert res.metrics["inferences_used"] == 1
