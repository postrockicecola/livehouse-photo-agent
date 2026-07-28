"""Single source of truth for deterministic gallery_search args.

Used by :mod:`services.agent.intent_router` (code path) so prompt text and router
rules cannot drift apart on magic numbers like ``min_score=70``.
"""
from __future__ import annotations

from typing import Any

# Shortlist / 交片 / 初选
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


def shortlist_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "min_score": SHORTLIST_MIN_SCORE,
        "exclude_trash": SHORTLIST_EXCLUDE_TRASH,
        "sort_by": SHORTLIST_SORT_BY,
        "limit": int(limit if limit is not None else SHORTLIST_DEFAULT_LIMIT),
    }


def quality_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "exclude_low_quality": QUALITY_EXCLUDE_LOW,
        "exclude_trash": QUALITY_EXCLUDE_TRASH,
        "sort_by": SHORTLIST_SORT_BY,
        "limit": int(limit if limit is not None else QUALITY_DEFAULT_LIMIT),
    }


def dedupe_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "dedupe_burst": True,
        "exclude_trash": SHORTLIST_EXCLUDE_TRASH,
        "sort_by": SHORTLIST_SORT_BY,
        "limit": int(limit if limit is not None else DEDUPE_DEFAULT_LIMIT),
    }


def sort_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return {
        "sort_by": SORT_BY_OVERALL,
        "limit": int(limit if limit is not None else SORT_DEFAULT_LIMIT),
    }
