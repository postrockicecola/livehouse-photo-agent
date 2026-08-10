"""Deterministic semantic planning for structured gallery-selection goals."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Optional

from services.agent import gallery_search_defaults as defaults

_SELECT_RE = re.compile(r"(选出|挑选|筛选|帮我(?:选|挑|找)|给我|找出|初选)")
_COUNT_RE = re.compile(r"(\d{1,3})\s*张")
_TENSION_RE = re.compile(r"(张力|戏剧性|冲击力)")
_XIAOHONGSHU_RE = re.compile(r"(小红书|小红薯|xhs|rednote)", re.IGNORECASE)

_SUBJECT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("吉他手", re.compile(r"(吉他手|吉他(?:演奏|solo|手部))", re.IGNORECASE)),
    ("贝斯手", re.compile(r"(贝斯手|bass\s*player)", re.IGNORECASE)),
    ("鼓手", re.compile(r"(鼓手|drummer)", re.IGNORECASE)),
    ("主唱", re.compile(r"(主唱|歌手|vocalist|singer)", re.IGNORECASE)),
    ("观众", re.compile(r"(观众|人群|crowd)", re.IGNORECASE)),
)

_TENSION_WEIGHTS: dict[str, float] = {
    "moment_peak": 0.40,
    "atmosphere_impact": 0.30,
    "light_color_character": 0.20,
    "composition_framing": 0.10,
}

_TENSION_CUES = (
    "dynamic_gesture",
    "expression_peak",
    "dramatic_lighting",
    "strong_composition",
)


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


def plan_selection_goal(user_text: str, *, default_count: int = 10) -> Optional[SelectionGoal]:
    """Parse supported semantic selection concepts into a stable goal object."""
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
    style = "tension" if _TENSION_RE.search(text) is not None else None
    platform = "xiaohongshu" if _XIAOHONGSHU_RE.search(text) is not None else None
    if style is None and platform is None:
        return None

    axes = tuple(_TENSION_WEIGHTS) if style == "tension" else ()
    cues = _TENSION_CUES if style == "tension" else ()
    weights = tuple(_TENSION_WEIGHTS.items()) if style == "tension" else ()
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

    if goal.style == "tension":
        args.update(
            {
                "sort_by": "moment_peak",
                "ranking_weights": dict(goal.ranking_weights),
                "rationale": (
                    "张力综合排序：优先动态/表情峰值、现场感染力、戏剧性光影与强构图"
                ),
            }
        )
    if goal.subject:
        args["query"] = goal.subject
    args["selection_goal"] = goal.to_dict()
    return args
