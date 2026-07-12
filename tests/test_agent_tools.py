"""Unit tests for the zero-cost planning tools: compare, cluster, query_gallery.

These read/annotate agent state only — no model, no inference cost — so they are
tested directly against a small in-memory :class:`AgentState`.
"""
from __future__ import annotations

from services.agent.tools import ClusterTool, CompareTool, QueryGalleryTool
from services.agent.types import ActionType, AgentConfig, AgentState, Candidate, ToolCall


def _state(specs: list[tuple[str, float]]) -> AgentState:
    cands = [
        Candidate(image_id=cid, image_path=f"/tmp/{cid}", features={"fast_score": fs})
        for cid, fs in specs
    ]
    return AgentState.from_candidates(cands, AgentConfig())


def test_compare_picks_higher_signal():
    state = _state([("a", 6.0), ("b", 9.0)])
    res = CompareTool().run(state, ToolCall(action=ActionType.COMPARE, args={"a_id": "a", "b_id": "b"}))
    assert res.ok
    assert res.observation["winner"] == "b"
    assert res.observation["basis"] == "fast_score"
    assert res.inference_cost == 0


def test_compare_prefers_analyzed_score_over_fast_score():
    state = _state([("a", 9.0), ("b", 1.0)])
    # a has only a cheap fast_score; b has a real analyzed score that beats it.
    state.candidates["b"].analysis = {"score": 95.0}
    state.candidates["b"].score = 95.0
    res = CompareTool().run(state, ToolCall(action=ActionType.COMPARE, args={"a_id": "a", "b_id": "b"}))
    assert res.observation["winner"] == "b"
    assert res.observation["basis"] == "fast_score/score"


def test_compare_rejects_identical_or_unknown():
    state = _state([("a", 6.0)])
    same = CompareTool().run(state, ToolCall(action=ActionType.COMPARE, args={"a_id": "a", "b_id": "a"}))
    unknown = CompareTool().run(state, ToolCall(action=ActionType.COMPARE, args={"a_id": "a", "b_id": "z"}))
    assert same.ok is False
    assert unknown.ok is False


def test_cluster_groups_bursts_and_marks_representative():
    # Two consecutive frames (a burst) + one far-away frame.
    state = _state([("DSC_0101.jpg", 7.0), ("DSC_0102.jpg", 9.0), ("DSC_0900.jpg", 5.0)])
    res = ClusterTool(window=3).run(state, ToolCall(action=ActionType.CLUSTER))
    assert res.ok
    assert res.observation["clusters"] == 2
    assert res.observation["multi_member_clusters"] == 1
    assert state.clustered is True

    c1 = state.candidates["DSC_0101.jpg"].features
    c2 = state.candidates["DSC_0102.jpg"].features
    far = state.candidates["DSC_0900.jpg"].features
    assert c1["cluster_id"] == c2["cluster_id"]  # same burst
    assert far["cluster_id"] != c1["cluster_id"]
    # Representative of the burst is the higher fast_score frame (0102 @ 9.0).
    assert c2["cluster_rep"] is True
    assert c1["cluster_rep"] is False


def test_query_gallery_hit_recalls_score_without_inference():
    state = _state([("a", 6.0)])

    def provider(image_id: str):
        return {"score": 88.0, "confidence": 0.9} if image_id == "a" else None

    res = QueryGalleryTool(provider).run(state, ToolCall(action=ActionType.QUERY_GALLERY, image_id="a"))
    assert res.ok
    assert res.observation["found"] is True
    assert res.inference_cost == 0
    cand = state.candidates["a"]
    assert cand.analyzed is True
    assert cand.score == 88.0
    assert cand.tier == "recall"
    assert cand.attempts == 0  # recall spends no inference budget
    assert cand.features["gallery_queried"] is True


def test_query_gallery_miss_marks_queried_only():
    state = _state([("a", 6.0)])
    res = QueryGalleryTool(provider=None).run(state, ToolCall(action=ActionType.QUERY_GALLERY, image_id="a"))
    assert res.ok
    assert res.observation["found"] is False
    cand = state.candidates["a"]
    assert cand.analyzed is False
    assert cand.features["gallery_queried"] is True
