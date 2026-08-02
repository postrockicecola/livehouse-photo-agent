"""Single source of truth for deterministic gallery_search args.

Recipe numbers live in ``data/agent/search_recipes.json`` so prompt text, router
rules, and gap tooling cannot drift on magic numbers like ``min_score=70``.

Used by :mod:`services.agent.intent_router` (code path).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RECIPES_PATH = _REPO_ROOT / "data" / "agent" / "search_recipes.json"

# Inline fallback mirrors search_recipes.json (used only if the data file is missing).
_FALLBACK_RECIPES: dict[str, dict[str, Any]] = {
    "shortlist": {
        "min_score": 70,
        "exclude_trash": True,
        "dedupe_burst": True,
        "sort_by": "overall",
        "default_limit": 10,
        "rationale": "按 overall 取高分短名单，并做连拍去重",
    },
    "social": {
        "min_score": 65,
        "min_deliverable": 7.0,
        "min_technical": 6.0,
        "exclude_trash": True,
        "exclude_low_quality": True,
        "dedupe_burst": True,
        "sort_by": "deliverable_subject",
        "default_limit": 10,
        "rationale": "适合社交分享：提高主体可用性与清晰度，按 deliverable 排序并去重",
    },
    "energy": {
        "min_score": 60,
        "min_atmosphere": 6.5,
        "exclude_trash": True,
        "dedupe_burst": True,
        "sort_by": "atmosphere_impact",
        "default_limit": 10,
        "rationale": "偏现场气氛与感染力，按 atmosphere_impact 排序并去重",
    },
    "peak": {
        "min_score": 60,
        "min_moment_peak": 6.5,
        "exclude_trash": True,
        "dedupe_burst": True,
        "sort_by": "moment_peak",
        "default_limit": 10,
        "rationale": "偏决定性瞬间，按 moment_peak 排序并去重",
    },
    "deliverable": {
        "min_score": 70,
        "min_deliverable": 7.0,
        "exclude_trash": True,
        "exclude_low_quality": True,
        "dedupe_burst": True,
        "sort_by": "overall",
        "default_limit": 10,
        "rationale": "交片向：overall 与主体可用性双门槛，连拍去重",
    },
    "quality": {
        "exclude_low_quality": True,
        "exclude_trash": True,
        "dedupe_burst": True,
        "sort_by": "overall",
        "default_limit": 20,
        "rationale": "剔除模糊/过曝等低质量帧后按 overall 排序",
    },
    "dedupe": {
        "dedupe_burst": True,
        "exclude_trash": True,
        "sort_by": "overall",
        "default_limit": 20,
        "rationale": "连拍/近重复只留代表帧",
    },
    "sort": {
        "sort_by": "overall",
        "default_limit": 20,
        "rationale": "按 overall 排序浏览",
    },
}


@lru_cache(maxsize=1)
def load_search_recipes() -> dict[str, dict[str, Any]]:
    """Load recipe table from JSON (or inline fallback)."""
    if _RECIPES_PATH.is_file():
        try:
            data = json.loads(_RECIPES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return {k: dict(v) for k, v in _FALLBACK_RECIPES.items()}


def _recipe(name: str) -> dict[str, Any]:
    recipes = load_search_recipes()
    return dict(recipes.get(name) or _FALLBACK_RECIPES.get(name) or {})


def _args_from_recipe(name: str, *, limit: int | None = None) -> dict[str, Any]:
    r = _recipe(name)
    out = {k: v for k, v in r.items() if k not in ("default_limit", "rationale")}
    out["recipe"] = name
    out["rationale"] = str(r.get("rationale") or name)
    default_limit = int(r.get("default_limit") or 10)
    out["limit"] = int(limit if limit is not None else default_limit)
    return out


# Back-compat constants (tests / docs may import these).
_short = _recipe("shortlist")
SHORTLIST_MIN_SCORE = float(_short.get("min_score") or 70)
SHORTLIST_EXCLUDE_TRASH = bool(_short.get("exclude_trash", True))
SHORTLIST_SORT_BY = str(_short.get("sort_by") or "overall")
SHORTLIST_DEFAULT_LIMIT = int(_short.get("default_limit") or 10)

_quality = _recipe("quality")
QUALITY_EXCLUDE_LOW = bool(_quality.get("exclude_low_quality", True))
QUALITY_EXCLUDE_TRASH = bool(_quality.get("exclude_trash", True))
QUALITY_DEFAULT_LIMIT = int(_quality.get("default_limit") or 20)

_dedupe = _recipe("dedupe")
DEDUPE_DEFAULT_LIMIT = int(_dedupe.get("default_limit") or 20)

_sort = _recipe("sort")
SORT_BY_OVERALL = str(_sort.get("sort_by") or "overall")
SORT_DEFAULT_LIMIT = int(_sort.get("default_limit") or 20)

_social = _recipe("social")
SOCIAL_MIN_SCORE = float(_social.get("min_score") or 65)
SOCIAL_MIN_DELIVERABLE = float(_social.get("min_deliverable") or 7.0)
SOCIAL_MIN_TECHNICAL = float(_social.get("min_technical") or 6.0)
SOCIAL_SORT_BY = str(_social.get("sort_by") or "deliverable_subject")

_energy = _recipe("energy")
ENERGY_MIN_SCORE = float(_energy.get("min_score") or 60)
ENERGY_MIN_ATMOSPHERE = float(_energy.get("min_atmosphere") or 6.5)
ENERGY_SORT_BY = str(_energy.get("sort_by") or "atmosphere_impact")

_peak = _recipe("peak")
PEAK_MIN_SCORE = float(_peak.get("min_score") or 60)
PEAK_MIN_MOMENT = float(_peak.get("min_moment_peak") or 6.5)
PEAK_SORT_BY = str(_peak.get("sort_by") or "moment_peak")

_deliverable = _recipe("deliverable")
DELIVERABLE_MIN_SCORE = float(_deliverable.get("min_score") or 70)
DELIVERABLE_MIN_DELIVERABLE = float(_deliverable.get("min_deliverable") or 7.0)
DELIVERABLE_SORT_BY = str(_deliverable.get("sort_by") or "overall")


def shortlist_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """Default 初选: overall shortlist with burst dedupe."""
    return _args_from_recipe("shortlist", limit=limit)


def social_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """朋友圈 / Ins / 社交媒体：主体可读 + 技术底线 + deliverable 排序。"""
    return _args_from_recipe("social", limit=limit)


def energy_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """最炸 / 气氛：按氛围感染力排序。"""
    return _args_from_recipe("energy", limit=limit)


def peak_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """高潮 / 决定性瞬间。"""
    return _args_from_recipe("peak", limit=limit)


def deliverable_search_args(*, limit: int | None = None) -> dict[str, Any]:
    """安全交片：overall + deliverable 双门槛。"""
    return _args_from_recipe("deliverable", limit=limit)


def quality_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return _args_from_recipe("quality", limit=limit)


def dedupe_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return _args_from_recipe("dedupe", limit=limit)


def sort_search_args(*, limit: int | None = None) -> dict[str, Any]:
    return _args_from_recipe("sort", limit=limit)
