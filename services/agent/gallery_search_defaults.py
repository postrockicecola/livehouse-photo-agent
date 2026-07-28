"""Single source of truth for deterministic gallery_search args.

Used by :mod:`services.agent.intent_router` (code path) so prompt text and router
rules cannot drift apart on magic numbers like ``min_score=70``.

Recipes map high-frequency intents to *different* Stage3 / score signals instead of
one bare overall top-K for every “选 N 张” ask.
"""
from __future__ import annotations

from typing import Any

# Bare shortlist / 初选
SHORTLIST_MIN_SCORE = 70
SHORTLIST_EXCLUDE_TRASH = True
SHORTLIST_SORT_BY = "overall"
SHORTLIST_DEFAULT_LIMIT = 10

# Clean shortlist after 剔糊 / 过曝
QUALITY_EXCLUDE_LOW = True
QUALITY_EXCLUDE_TRASH = True
QUALITY_DEFAULT_LIMIT = 20

# Burst / 连拍 dedupe
DEDUPE_DEFAULT_LIMIT = 20

# Sort-only
SORT_BY_OVERALL = "overall"
SORT_DEFAULT_LIMIT = 20

# Social / 朋友圈 / Ins — prefer client-readable subjects, not peak chaos.
SOCIAL_MIN_SCORE = 65
SOCIAL_MIN_DELIVERABLE = 7.0
SOCIAL_MIN_TECHNICAL = 6.0
SOCIAL_SORT_BY = "deliverable_subject"

# 最炸 / 气氛 — atmosphere / energy first.
ENERGY_MIN_SCORE = 60
ENERGY_MIN_ATMOSPHERE = 6.5
ENERGY_SORT_BY = "atmosphere_impact"

# 高潮瞬间
PEAK_MIN_SCORE = 60
PEAK_MIN_MOMENT = 6.5
PEAK_SORT_BY = "moment_peak"

# Safe 交片 — overall + deliverable dual floor.
DELIVERABLE_MIN_SCORE = 70
DELIVERABLE_MIN_DELIVERABLE = 7.0
DELIVERABLE_SORT_BY = "overall"


def _base(*, limit: int, sort_by: str, recipe: str, rationale: str, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "exclude_trash": SHORTLIST_EXCLUDE_TRASH,
        "dedupe_burst": True,
        "sort_by": sort_by,
        "limit": int(limit),
        "recipe": recipe,
        "rationale": rationale,
    }
    out.update(extra)
    return out


def shortlist_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """Default 初选: overall shortlist with burst dedupe."""
    return _base(
        limit=int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
        sort_by=SHORTLIST_SORT_BY,
        recipe="shortlist",
        rationale="按 overall 取高分短名单，并做连拍去重",
        min_score=SHORTLIST_MIN_SCORE,
    )


def social_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """朋友圈 / Ins / 社交媒体：主体可读 + 技术底线 + deliverable 排序。"""
    return _base(
        limit=int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
        sort_by=SOCIAL_SORT_BY,
        recipe="social",
        rationale="适合社交分享：提高主体可用性与清晰度，按 deliverable 排序并去重",
        min_score=SOCIAL_MIN_SCORE,
        min_deliverable=SOCIAL_MIN_DELIVERABLE,
        min_technical=SOCIAL_MIN_TECHNICAL,
        exclude_low_quality=True,
    )


def energy_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """最炸 / 气氛：按氛围感染力排序。"""
    return _base(
        limit=int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
        sort_by=ENERGY_SORT_BY,
        recipe="energy",
        rationale="偏现场气氛与感染力，按 atmosphere_impact 排序并去重",
        min_score=ENERGY_MIN_SCORE,
        min_atmosphere=ENERGY_MIN_ATMOSPHERE,
    )


def peak_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """高潮 / 决定性瞬间。"""
    return _base(
        limit=int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
        sort_by=PEAK_SORT_BY,
        recipe="peak",
        rationale="偏决定性瞬间，按 moment_peak 排序并去重",
        min_score=PEAK_MIN_SCORE,
        min_moment_peak=PEAK_MIN_MOMENT,
    )


def deliverable_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """安全交片：overall + deliverable 双门槛。"""
    return _base(
        limit=int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
        sort_by=DELIVERABLE_SORT_BY,
        recipe="deliverable",
        rationale="交片向：overall 与主体可用性双门槛，连拍去重",
        min_score=DELIVERABLE_MIN_SCORE,
        min_deliverable=DELIVERABLE_MIN_DELIVERABLE,
        exclude_low_quality=True,
    )


def quality_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "exclude_low_quality": QUALITY_EXCLUDE_LOW,
        "exclude_trash": QUALITY_EXCLUDE_TRASH,
        "dedupe_burst": True,
        "sort_by": SHORTLIST_SORT_BY,
        "limit": int(limit if limit is not None else QUALITY_DEFAULT_LIMIT),
        "recipe": "quality",
        "rationale": "剔除模糊/过曝等低质量帧后按 overall 排序",
    }


def dedupe_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "dedupe_burst": True,
        "exclude_trash": SHORTLIST_EXCLUDE_TRASH,
        "sort_by": SHORTLIST_SORT_BY,
        "limit": int(limit if limit is not None else DEDUPE_DEFAULT_LIMIT),
        "recipe": "dedupe",
        "rationale": "连拍/近重复只留代表帧",
    }


def sort_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "sort_by": SORT_BY_OVERALL,
        "limit": int(limit if limit is not None else SORT_DEFAULT_LIMIT),
        "recipe": "sort",
        "rationale": "按 overall 排序浏览",
    }
