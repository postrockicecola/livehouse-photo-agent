"""Deterministic gallery intent router (no LLM) — fixtures + table-driven regressions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import pytest

from services.agent.gallery_search_defaults import (
    energy_search_args,
    shortlist_search_args,
    social_search_args,
)
from services.agent.intent_router import RouteMatch, route_gallery_intent

_FIXTURES = Path(__file__).parent / "agent" / "fixtures" / "intent_router_cases.json"
_ROUTER_CASES = Path(__file__).parent / "agent" / "fixtures" / "router_cases.jsonl"


def _cases() -> list[dict]:
    return json.loads(_FIXTURES.read_text(encoding="utf-8"))


def _router_jsonl_cases() -> list[dict]:
    """Tier-1 production→regression cases (never delete; skip ``deprecated``)."""
    if not _ROUTER_CASES.is_file():
        return []
    rows: list[dict] = []
    for i, line in enumerate(_ROUTER_CASES.read_text(encoding="utf-8").splitlines()):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict) or obj.get("deprecated"):
            continue
        obj["_id"] = str(obj.get("source") or f"router_{i}") + f"_{i}"
        rows.append(obj)
    return rows


def _assert_route(
    user: str,
    *,
    expect_rule: Optional[str],
    expect_limit: Optional[int] = None,
    expect_select_after: Optional[bool] = None,
) -> Optional[RouteMatch]:
    match = route_gallery_intent(user)
    if expect_rule is None:
        assert match is None, f"expected None for {user!r}, got {match}"
        return None
    assert match is not None, f"expected rule {expect_rule!r} for {user!r}"
    assert match.rule_id == expect_rule
    assert match.calls, "expected at least one routed call"
    if expect_limit is not None:
        assert match.calls[0].args.get("limit") == expect_limit
    if expect_select_after is not None:
        assert match.select_after_search is expect_select_after
    return match


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_intent_router_fixtures(case: dict) -> None:
    match = route_gallery_intent(case["user"])
    expect_rule = case.get("expect_rule")
    if expect_rule is None:
        assert match is None
        return
    assert match is not None
    assert match.rule_id == expect_rule
    assert match.select_after_search is bool(case.get("expect_select_after"))
    assert match.calls, "expected at least one routed call"
    call = match.calls[0]
    assert call.tool == case["expect_tool"]
    for key, val in (case.get("expect_args") or {}).items():
        assert call.args.get(key) == val, f"{key}: {call.args.get(key)!r} != {val!r}"


@pytest.mark.parametrize("case", _router_jsonl_cases(), ids=lambda c: c.get("_id", "row"))
def test_intent_router_production_regression_jsonl(case: dict) -> None:
    """CI Tier-1: cases promoted from the weekly review queue."""
    match = route_gallery_intent(str(case.get("input") or ""))
    expect_rule = case.get("expected_rule_id")
    if expect_rule is None:
        assert match is None
        return
    assert match is not None
    assert match.rule_id == expect_rule
    assert match.calls
    for key, val in (case.get("expected_args") or {}).items():
        assert match.calls[0].args.get(key) == val


# --- Table-driven regressions (problem 1 / 2 / compound / semantic fallthrough) ---

_TABLE: list[tuple[str, str, Optional[str], Optional[int], Optional[bool]]] = [
    # colloquial shortlist (prefix parity with _LIMIT_RE)
    ("colloquial_bang_xuan", "帮我选20张照片", "shortlist_select", 20, True),
    ("colloquial_zhaochu", "找出15张给我", "shortlist_select", 15, True),
    ("colloquial_geiwo", "给我10张精选", "shortlist_select", 10, True),
    # negation → None
    ("neg_sort", "不要按分数排序，我想看原始顺序", None, None, None),
    ("neg_dedupe", "别自动去重", None, None, None),
    ("neg_dedupe_long", "别自动去重，我要看所有连拍", None, None, None),
    ("neg_quality", "不用剔除模糊的", None, None, None),
    # positive controls for negated rules
    ("pos_sort", "按分数排序", "sort_overall", 20, False),
    ("pos_dedupe", "连拍只留一张", "dedupe_burst", 20, False),
    ("pos_quality", "剔糊", "exclude_low_quality", 20, False),
    # compound select_after_search
    ("compound_dedupe_select", "去重之后选出20张", "dedupe_burst", 20, True),
    ("compound_quality_jiaopian", "剔糊之后交片", "exclude_low_quality", 20, True),
    # semantic / compound+semantic → LLM (film vibe is now deterministically routed)
    ("semantic_guitar", "找吉他手的照片", None, None, None),
    ("semantic_film", "帮我修成复古胶片风格看看", "apply_film_vibe", None, False),
    ("film_recommend", "修成最适合这张图的胶片感", "recommend_film_for_photo", None, False),
    ("film_color_intense", "颜色再浓烈一些", "apply_film_vibe", None, False),
    ("film_sat_max", "饱和度拉满", "apply_film_vibe", None, False),
    # Compound select + semantic residue → shortlist with query (hybrid path).
    ("shortlist_plus_panorama", "选出20张，但多给我一些全景镜头", "shortlist_select", 20, True),
]


@pytest.mark.parametrize(
    "case_id,user,expect_rule,expect_limit,expect_select_after",
    _TABLE,
    ids=[t[0] for t in _TABLE],
)
def test_intent_router_table(
    case_id: str,
    user: str,
    expect_rule: Optional[str],
    expect_limit: Optional[int],
    expect_select_after: Optional[bool],
) -> None:
    _ = case_id
    _assert_route(
        user,
        expect_rule=expect_rule,
        expect_limit=expect_limit,
        expect_select_after=expect_select_after,
    )


def test_shortlist_defaults_single_source() -> None:
    """Router args come from gallery_search_defaults, not duplicated literals."""
    m = route_gallery_intent("选出8张")
    assert m is not None
    assert m.calls[0].args == shortlist_search_args(limit=8)


def test_social_and_energy_recipes_single_source() -> None:
    social = route_gallery_intent("帮我挑选10张适合发朋友圈的")
    assert social is not None
    assert social.rule_id == "shortlist_social"
    assert social.calls[0].args == social_search_args(limit=10)
    assert social.select_after_search is True

    energy = route_gallery_intent("帮我选出最炸的10张")
    assert energy is not None
    assert energy.rule_id == "shortlist_energy"
    assert energy.calls[0].args == energy_search_args(limit=10)


def test_compound_energy_attaches_semantic_query() -> None:
    from services.agent.intent_router import semantic_residue

    assert semantic_residue("帮我选出最炸的10张") == ""
    assert semantic_residue("最炸的吉他手") == "吉他手"
    assert semantic_residue("找出吉他手弹琴的10张") == "吉他手弹琴"
    m = route_gallery_intent("最炸的吉他手")
    assert m is not None
    assert m.rule_id == "shortlist_energy"
    assert m.calls[0].args.get("query") == "吉他手"
    assert m.calls[0].args.get("recipe") == "energy"

    guitar = route_gallery_intent("找出吉他手弹琴的10张")
    assert guitar is not None
    assert guitar.rule_id == "shortlist_select"
    assert guitar.calls[0].args.get("query") == "吉他手弹琴"


def test_contrast_shortlist_attaches_panorama_query() -> None:
    m = route_gallery_intent("选出20张，但多给我一些全景镜头")
    assert m is not None
    assert m.rule_id == "shortlist_select"
    assert m.calls[0].args.get("query") == "全景"
    assert m.select_after_search is True


def test_count_prefix_shared_constant() -> None:
    """_LIMIT_RE and _SELECT_SHORTLIST_RE must share _COUNT_PREFIX (no drift)."""
    import services.agent.intent_router as mod

    assert hasattr(mod, "_COUNT_PREFIX")
    assert mod._COUNT_PREFIX in mod._LIMIT_RE.pattern
    assert mod._COUNT_PREFIX in mod._SELECT_SHORTLIST_RE.pattern


def test_routed_chat_skips_tool_llm(tmp_path: Any, monkeypatch: Any) -> None:
    """End-to-end: shortlist route executes tools without asking the model for JSON."""
    from services.agent.conversation import ConversationalAgent, ConversationMemory
    from services.agent.skills.gallery import gallery_registry

    rows = [
        {
            "file": f"p{i}.jpg",
            "overall_score": 90.0 - i,
            "scores": {"overall": 90.0 - i, "energy": 8.0, "technical": 8.0, "composition": 8.0},
            "energy": 8.0,
            "technical": 8.0,
            "composition": 8.0,
            "category": "AI_Best_90+",
            "tags": ["stage"],
            "reason": "ok",
        }
        for i in range(12)
    ]
    (tmp_path / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")

    llm_calls: list[list[dict]] = []

    def chat_fn(messages: list[dict[str, str]]) -> str:
        llm_calls.append(list(messages))
        return "已选出 top 照片。"

    agent = ConversationalAgent(
        chat_fn,
        memory=ConversationMemory(system_prompt="test"),
        skills=gallery_registry(str(tmp_path)),
        wrap_tool_output=False,
        max_tool_rounds=3,
    )
    result = agent.chat("选出10张交片")
    assert result.tool_calls
    assert result.tool_calls[0]["tool"] == "gallery_search"
    assert result.tool_calls[0]["args"]["limit"] == 10
    assert result.tool_calls[0]["metadata"].get("routed") == "shortlist_deliverable"
    assert result.tool_calls[0]["args"].get("recipe") == "deliverable"
    assert any(tc["tool"] == "gallery_select" for tc in result.tool_calls)
    assert len(llm_calls) == 1
    assert "选出" in result.reply or "照片" in result.reply
