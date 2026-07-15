"""Tests for agent finalize diversity (at most one keeper per burst/scene cluster)."""
from __future__ import annotations

from services.agent.tools import FinalizeTool, diversify_keeper_selection
from services.agent.types import ActionType, AgentConfig, AgentState, Candidate, ToolCall
from services.diversity_selector import diversify_keeper_ids, diversity_settings


def test_diversity_settings_agent_finalize_defaults():
    s = diversity_settings({"processing": {"diversity_selection": {}}})
    assert s["finalize_enabled"] is True
    assert s["max_per_cluster"] == 1
    assert s["burst_window"] == 3


def test_diversify_keeper_ids_burst_window_keeps_one_per_cluster():
    items = [
        {"id": "DSC_0101.jpg", "score": 90.0},
        {"id": "DSC_0102.jpg", "score": 95.0},
        {"id": "DSC_0103.jpg", "score": 88.0},
        {"id": "DSC_0900.jpg", "score": 80.0},
    ]
    settings = diversity_settings(None)
    settings["enabled"] = False  # force burst fallback (no CLIP paths)
    kept, meta = diversify_keeper_ids(
        items,
        ["DSC_0101.jpg", "DSC_0102.jpg", "DSC_0103.jpg", "DSC_0900.jpg"],
        target=10,
        settings=settings,
    )
    assert meta["signal"] == "burst_window"
    assert kept == ["DSC_0101.jpg", "DSC_0900.jpg"]  # first proposed wins the burst cluster
    assert "DSC_0102.jpg" in meta["dropped"]
    assert "DSC_0103.jpg" in meta["dropped"]


def test_diversify_keeper_ids_refills_from_other_clusters():
    items = [
        {"id": "DSC_0101.jpg", "score": 99.0},
        {"id": "DSC_0102.jpg", "score": 98.0},
        {"id": "DSC_0500.jpg", "score": 70.0},
        {"id": "DSC_0800.jpg", "score": 85.0},
    ]
    settings = diversity_settings(None)
    settings["enabled"] = False
    kept, meta = diversify_keeper_ids(
        items,
        ["DSC_0101.jpg", "DSC_0102.jpg"],  # near-dup burst
        target=2,
        settings=settings,
        fill_ids=["DSC_0500.jpg", "DSC_0800.jpg"],
    )
    assert kept[0] == "DSC_0101.jpg"
    assert kept[1] == "DSC_0800.jpg"  # refill prefers higher score outside the dropped burst
    assert meta["after"] == 2


def test_diversify_keeper_ids_respects_cluster_id():
    items = [
        {"id": "a", "score": 90.0, "cluster_id": 0},
        {"id": "b", "score": 88.0, "cluster_id": 0},
        {"id": "c", "score": 70.0, "cluster_id": 1},
    ]
    settings = diversity_settings(None)
    settings["enabled"] = False
    kept, meta = diversify_keeper_ids(
        items, ["a", "b", "c"], target=10, settings=settings
    )
    assert meta["signal"] == "features_cluster_id"
    assert kept == ["a", "c"]


def test_finalize_tool_drops_near_dup_burst():
    cands = [
        Candidate(image_id="DSC_0101.jpg", image_path="/tmp/DSC_0101.jpg", score=90.0),
        Candidate(image_id="DSC_0102.jpg", image_path="/tmp/DSC_0102.jpg", score=95.0),
        Candidate(image_id="DSC_0900.jpg", image_path="/tmp/DSC_0900.jpg", score=80.0),
    ]
    for c in cands:
        c.analysis = {"score": c.score, "dimensions": {}}
    state = AgentState.from_candidates(cands, AgentConfig(target_keepers=10, keep_score_threshold=70.0))
    res = FinalizeTool(diversify=True).run(
        state,
        ToolCall(
            action=ActionType.FINALIZE,
            args={"selected": ["DSC_0101.jpg", "DSC_0102.jpg", "DSC_0900.jpg"]},
        ),
    )
    assert res.ok
    assert state.selected == ["DSC_0101.jpg", "DSC_0900.jpg"]
    assert res.observation["diversity"]["signal"] == "burst_window"
    assert "DSC_0102.jpg" in res.observation["diversity"]["dropped"]


def test_finalize_tool_can_disable_diversify():
    cands = [
        Candidate(image_id="DSC_0101.jpg", image_path="/tmp/x", score=90.0),
        Candidate(image_id="DSC_0102.jpg", image_path="/tmp/y", score=95.0),
    ]
    for c in cands:
        c.analysis = {"score": c.score}
    state = AgentState.from_candidates(cands, AgentConfig())
    res = FinalizeTool(diversify=False).run(
        state,
        ToolCall(action=ActionType.FINALIZE, args={"selected": ["DSC_0101.jpg", "DSC_0102.jpg"]}),
    )
    assert state.selected == ["DSC_0101.jpg", "DSC_0102.jpg"]
    assert "diversity" not in res.observation


def test_diversify_keeper_selection_disabled():
    cands = [
        Candidate(image_id="DSC_0101.jpg", image_path="/tmp/x", score=90.0),
        Candidate(image_id="DSC_0102.jpg", image_path="/tmp/y", score=95.0),
    ]
    settings = diversity_settings(None)
    settings["finalize_enabled"] = False
    kept, meta = diversify_keeper_selection(
        cands, ["DSC_0101.jpg", "DSC_0102.jpg"], target=10, settings=settings
    )
    assert kept == ["DSC_0101.jpg", "DSC_0102.jpg"]
    assert meta["signal"] == "disabled"
