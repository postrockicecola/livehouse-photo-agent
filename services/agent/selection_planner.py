"""Deterministic semantic planning for structured gallery-selection goals."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from services.agent import gallery_search_defaults as defaults

_SELECT_RE = re.compile(r"(选出|挑选|筛选|帮我(?:选|挑|找)|给我|找出|初选)")
_COUNT_RE = re.compile(r"(\d{1,3})\s*张")
_XIAOHONGSHU_RE = re.compile(r"(小红书|小红薯|xhs|rednote)", re.IGNORECASE)

# Scene recipes (朋友圈 / 最炸 / 高潮 / 交片) stay in intent_router.
# Planner only owns styles that those recipes do not already claim.
_SUBJECT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("吉他手", re.compile(r"(吉他手|吉他(?:演奏|solo|手部))", re.IGNORECASE)),
    ("贝斯手", re.compile(r"(贝斯手|bass\s*player)", re.IGNORECASE)),
    ("鼓手", re.compile(r"(鼓手|drummer)", re.IGNORECASE)),
    ("主唱", re.compile(r"(主唱|歌手|vocalist|singer)", re.IGNORECASE)),
    ("萨克斯", re.compile(r"(萨克斯|sax(?:ophone)?)", re.IGNORECASE)),
    ("键盘手", re.compile(r"(键盘手|键盘|keyboard)", re.IGNORECASE)),
    ("观众", re.compile(r"(观众|人群|crowd)", re.IGNORECASE)),
    ("全景", re.compile(r"(舞台全景|全景舞台|全景)", re.IGNORECASE)),
    ("前排", re.compile(r"(前排|front\s*row)", re.IGNORECASE)),
)

_STYLE_SPECS: dict[str, dict[str, Any]] = {
    "tension": {
        "pattern": re.compile(r"(张力|戏剧性|冲击力)"),
        "sort_by": "moment_peak",
        "weights": {
            "moment_peak": 0.40,
            "atmosphere_impact": 0.30,
            "light_color_character": 0.20,
            "composition_framing": 0.10,
        },
        "cues": (
            "dynamic_gesture",
            "expression_peak",
            "dramatic_lighting",
            "strong_composition",
        ),
        "rationale": "张力综合排序：优先动态/表情峰值、现场感染力、戏剧性光影与强构图",
    },
    "backlight": {
        "pattern": re.compile(r"(逆光|轮廓光|backlight)", re.IGNORECASE),
        "sort_by": "light_color_character",
        "weights": {
            "light_color_character": 0.50,
            "composition_framing": 0.30,
            "moment_peak": 0.20,
        },
        "cues": ("rim_light", "silhouette", "dramatic_lighting"),
        "rationale": "逆光/轮廓光优先：按光色性格排序，保留剪影与轮廓",
    },
    "solitude": {
        "pattern": re.compile(r"(孤独感|孤独|宁静忧郁|忧郁|lonely)", re.IGNORECASE),
        "sort_by": "atmosphere_impact",
        "weights": {
            "atmosphere_impact": 0.50,
            "composition_framing": 0.30,
            "light_color_character": 0.20,
        },
        "cues": ("empty_stage", "sparse_frame", "quiet_mood"),
        "rationale": "孤独/宁静氛围优先：按现场气氛排序，偏向留白与疏离",
    },
    "cinematic": {
        "pattern": re.compile(r"(电影感|cinematic)", re.IGNORECASE),
        "sort_by": "composition_framing",
        "weights": {
            "composition_framing": 0.40,
            "light_color_character": 0.35,
            "moment_peak": 0.25,
        },
        "cues": ("strong_composition", "dramatic_lighting", "color_grade"),
        "rationale": "电影感优先：强构图与光色，再看瞬间",
    },
}

# First listed style wins when several cues appear.
_STYLE_ORDER = ("tension", "backlight", "solitude", "cinematic")


@dataclass(frozen=True)
class SelectionGoal:
    """Normalized user intent before it is compiled to gallery_search args."""

    action: str
    count: int
    subject: Optional[str] = None
    style: Optional[str] = None
    platform: Optional[str] = None
    aesthetic_axes: tuple[str, ...] = ()
    visual_cues: tuple[str, ...] = ()
    ranking_weights: tuple[tuple[str, float], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["aesthetic_axes"] = list(self.aesthetic_axes)
        data["visual_cues"] = list(self.visual_cues)
        data["ranking_weights"] = dict(self.ranking_weights)
        return data


def _match_style(text: str) -> Optional[str]:
    for name in _STYLE_ORDER:
        if _STYLE_SPECS[name]["pattern"].search(text) is not None:
            return name
    return None


def plan_selection_goal(user_text: str, *, default_count: int = 10) -> Optional[SelectionGoal]:
    """Parse supported semantic selection concepts into a stable goal object.

    Requires a select verb plus a style or platform. Subject-only asks stay on
    the recipe / residue router so「找出吉他手弹琴的10张」does not steal
    ``shortlist_select``.
    """
    text = (user_text or "").strip()
    if not text or _SELECT_RE.search(text) is None:
        return None

    count_match = _COUNT_RE.search(text)
    count = int(count_match.group(1)) if count_match else int(default_count)
    count = max(1, min(100, count))

    subject = next(
        (name for name, pattern in _SUBJECT_PATTERNS if pattern.search(text) is not None),
        None,
    )
    style = _match_style(text)
    platform = "xiaohongshu" if _XIAOHONGSHU_RE.search(text) is not None else None
    if style is None and platform is None:
        return None

    spec = _STYLE_SPECS.get(style or "") or {}
    weights = tuple((spec.get("weights") or {}).items())
    cues = tuple(spec.get("cues") or ())
    axes = tuple((spec.get("weights") or {}))
    return SelectionGoal(
        action="select",
        count=count,
        subject=subject,
        style=style,
        platform=platform,
        aesthetic_axes=axes,
        visual_cues=cues,
        ranking_weights=weights,
    )


def compile_selection_goal(goal: SelectionGoal) -> dict[str, Any]:
    """Compile a normalized goal to existing gallery_search filters and ranking."""
    if goal.platform == "xiaohongshu":
        args = defaults.social_search_args(limit=goal.count)
    else:
        args = defaults.shortlist_search_args(limit=goal.count)

    spec = _STYLE_SPECS.get(goal.style or "") or {}
    if spec:
        args.update(
            {
                "sort_by": spec["sort_by"],
                "ranking_weights": dict(goal.ranking_weights),
                "rationale": spec["rationale"],
            }
        )
    if goal.subject:
        args["query"] = goal.subject
    args["selection_goal"] = goal.to_dict()
    return args


def apply_selection_experiences(
    args: dict[str, Any],
    experiences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compile retrieved rejection reasons into deterministic search constraints."""
    if not experiences:
        return args
    out = dict(args)
    reasons = {
        str(item.get("reason_code") or "")
        for item in experiences
        if str(item.get("decision") or "") == "rejected"
    }
    rejected_files = {
        str(file)
        for item in experiences
        if str(item.get("decision") or "") == "rejected"
        for file in (item.get("files") or [])
        if str(file).strip()
    }
    if "too_dark" in reasons:
        out["min_exposure_control"] = max(float(out.get("min_exposure_control") or 0), 6.0)
    if "blurry" in reasons:
        out["min_technical"] = max(float(out.get("min_technical") or 0), 6.0)
        out["exclude_low_quality"] = True
    if "weak_moment" in reasons:
        out["min_moment_peak"] = max(float(out.get("min_moment_peak") or 0), 6.0)
    if "poor_subject" in reasons:
        out["min_deliverable"] = max(float(out.get("min_deliverable") or 0), 6.0)
    if rejected_files:
        existing = {str(value) for value in (out.get("exclude_files") or [])}
        out["exclude_files"] = sorted(existing | rejected_files)
    out["experience_context"] = [
        {
            "experience_id": item.get("experience_id") or item.get("id"),
            "decision": item.get("decision"),
            "reason_code": item.get("reason_code"),
            "feedback": item.get("feedback"),
        }
        for item in experiences[:5]
    ]
    return out
