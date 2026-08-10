from __future__ import annotations

from services.agent.intent_router import route_gallery_intent
from services.agent.selection_planner import plan_selection_goal
from services.agent.skills.gallery_common import _filter_rows


def test_tension_xiaohongshu_request_becomes_structured_goal() -> None:
    text = "帮我选10张最有张力的吉他手照片发小红书"
    goal = plan_selection_goal(text)

    assert goal is not None
    assert goal.action == "select"
    assert goal.count == 10
    assert goal.subject == "吉他手"
    assert goal.style == "tension"
    assert goal.platform == "xiaohongshu"
    assert dict(goal.ranking_weights) == {
        "moment_peak": 0.4,
        "atmosphere_impact": 0.3,
        "light_color_character": 0.2,
        "composition_framing": 0.1,
    }


def test_tension_xiaohongshu_goal_compiles_to_search_route() -> None:
    match = route_gallery_intent("帮我选10张最有张力的吉他手照片发小红书")

    assert match is not None
    assert match.rule_id == "shortlist_semantic_goal"
    assert match.select_after_search is True
    args = match.calls[0].args
    assert args["recipe"] == "social"
    assert args["limit"] == 10
    assert args["query"] == "吉他手"
    assert args["sort_by"] == "moment_peak"
    assert args["selection_goal"]["platform"] == "xiaohongshu"
    assert args["selection_goal"]["visual_cues"] == [
        "dynamic_gesture",
        "expression_peak",
        "dramatic_lighting",
        "strong_composition",
    ]


def test_weighted_tension_ranking_uses_all_aesthetic_axes() -> None:
    peak_only = {
        "file": "peak.jpg",
        "overall_score": 80,
        "dimensions": {
            "moment_peak": 10,
            "atmosphere_impact": 1,
            "light_color_character": 1,
            "composition_framing": 1,
        },
    }
    balanced = {
        "file": "balanced.jpg",
        "overall_score": 80,
        "dimensions": {
            "moment_peak": 8,
            "atmosphere_impact": 8,
            "light_color_character": 8,
            "composition_framing": 8,
        },
    }
    ranked = _filter_rows(
        [peak_only, balanced],
        {
            "_sort_by": "moment_peak",
            "ranking_weights": {
                "moment_peak": 0.4,
                "atmosphere_impact": 0.3,
                "light_color_character": 0.2,
                "composition_framing": 0.1,
            },
        },
    )

    assert [row["file"] for row in ranked] == ["balanced.jpg", "peak.jpg"]
