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


def test_backlight_drummer_becomes_structured_goal() -> None:
    goal = plan_selection_goal("选出8张逆光的鼓手")
    assert goal is not None
    assert goal.subject == "鼓手"
    assert goal.style == "backlight"
    assert goal.count == 8
    assert goal.platform is None

    match = route_gallery_intent("选出8张逆光的鼓手")
    assert match is not None
    assert match.rule_id == "shortlist_semantic_goal"
    assert match.select_after_search is True
    args = match.calls[0].args
    assert args["query"] == "鼓手"
    assert args["sort_by"] == "light_color_character"
    assert args["selection_goal"]["style"] == "backlight"


def test_solitude_select_does_not_require_platform() -> None:
    goal = plan_selection_goal("帮我选10张有孤独感的")
    assert goal is not None
    assert goal.style == "solitude"
    assert goal.subject is None
    match = route_gallery_intent("帮我选10张有孤独感的")
    assert match is not None
    assert match.rule_id == "shortlist_semantic_goal"
    assert match.calls[0].args["sort_by"] == "atmosphere_impact"


def test_cinematic_subject_compiles() -> None:
    match = route_gallery_intent("选出10张电影感的鼓手")
    assert match is not None
    assert match.rule_id == "shortlist_semantic_goal"
    assert match.calls[0].args["query"] == "鼓手"
    assert match.calls[0].args["sort_by"] == "composition_framing"


def test_new_styles_do_not_steal_recipe_or_subject_only_routes() -> None:
    social = route_gallery_intent("帮我挑选10张适合发朋友圈的")
    assert social is not None
    assert social.rule_id == "shortlist_social"
    energy = route_gallery_intent("帮我选出最炸的10张")
    assert energy is not None
    assert energy.rule_id == "shortlist_energy"
    guitar = route_gallery_intent("找出吉他手弹琴的10张")
    assert guitar is not None
    assert guitar.rule_id == "shortlist_select"
    assert guitar.calls[0].args.get("query") == "吉他手弹琴"
    assert plan_selection_goal("找出吉他手弹琴的10张") is None


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
