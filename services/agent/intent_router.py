"""Deterministic intent → tool routing for Gallery Copilot.

High-frequency requests whose args can be extracted with regex/keywords
(选出 N 张, 连拍去重, 按分数排序, 剔糊/过曝) are mapped here and skip the LLM
tool-call round. Semantic / fuzzy intents (吉他手, 胶片风格, …) return ``None``
so the existing JSON-in-text agent loop handles them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from services.agent import gallery_search_defaults as defaults

# Shared count-phrase prefixes — used by both limit extraction and shortlist routing
# so "帮我选20张" / "找出15张" stay in sync.
_COUNT_PREFIX = r"(?:选出|找|找出|给我|帮我(?:找|选)?|筛选|选)"

_LIMIT_RE = re.compile(rf"(?:{_COUNT_PREFIX})?\s*(\d{{1,3}})\s*张")
# 选出N张 (same prefixes) / 初选 / 交片 → search + gallery_select
_SELECT_SHORTLIST_RE = re.compile(rf"(?:{_COUNT_PREFIX}\s*\d{{1,3}}\s*张|初选|交片)")
# 发 Ins / 适合法发…N张 → score shortlist search only (no auto-select)
_DELIVERABLE_RE = re.compile(
    r"(适合法发|发\s*ins|发ins|instagram|交片级)",
    re.IGNORECASE,
)
_DEDUPE_RE = re.compile(r"(连拍.*(?:留一张|去重|只留)|去重|burst\s*dedup)", re.IGNORECASE)
_SORT_RE = re.compile(r"(按分数排序|按得分排序|按\s*overall\s*排序|按分数排)")
_QUALITY_RE = re.compile(
    r"(剔糊|去糊|过曝|剔除模糊|排除(?:模糊|过曝)|exclude[_\s-]?low[_\s-]?quality)",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"(不要|别|不用|无需|不需要)")
# Shortlist + contrastive semantic clause → leave to LLM (no smart split).
_CONTRAST_SEMANTIC_RE = re.compile(
    r"(?:但|不过|但是).*(?:全景|吉他|鼓手|贝斯|胶片|风格|逆光|前排|慢门|特写|歌手|舞台)"
)

_NEGATION_WINDOW = 5


@dataclass(frozen=True)
class RoutedCall:
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class RouteMatch:
    """A deterministic route hit.

    ``select_after_search``: after ``gallery_search`` succeeds, also run
    ``gallery_select`` on the returned ``files`` (选出 / 初选 / 交片).
    """

    rule_id: str
    calls: list[RoutedCall] = field(default_factory=list)
    select_after_search: bool = False


def _is_negated(text: str, match: re.Match[str], *, window: int = _NEGATION_WINDOW) -> bool:
    """True if a negation cue appears in the ``window`` chars immediately before ``match``."""
    start = match.start()
    lo = max(0, start - max(1, int(window)))
    return _NEGATION_RE.search(text[lo:start]) is not None


def _positive_match(text: str, pattern: re.Pattern[str]) -> Optional[re.Match[str]]:
    """First regex match that is not locally negated; ``None`` if all hits are negated."""
    for m in pattern.finditer(text):
        if not _is_negated(text, m):
            return m
    return None


def _extract_limit(text: str, fallback: int) -> int:
    m = _LIMIT_RE.search(text)
    if not m:
        return fallback
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return fallback
    return max(1, min(100, n))


def has_count_shortlist_phrase(user_text: str) -> bool:
    """True when text contains an extractable ``N张`` count phrase (coverage probe)."""
    return bool(_LIMIT_RE.search((user_text or "").strip()))


def route_gallery_intent(user_text: str) -> Optional[RouteMatch]:
    """Return a :class:`RouteMatch` if ``user_text`` is a deterministic intent.

    Order: dedupe / quality / sort before shortlist so compound phrases prefer the
    more specific filter; shortlist and deliverable patterns last.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    wants_select = _positive_match(text, _SELECT_SHORTLIST_RE) is not None

    dedupe_m = _positive_match(text, _DEDUPE_RE)
    if dedupe_m is not None:
        limit = _extract_limit(text, defaults.DEDUPE_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="dedupe_burst",
            calls=[RoutedCall("gallery_search", defaults.dedupe_search_args(limit=limit))],
            select_after_search=wants_select,
        )

    quality_m = _positive_match(text, _QUALITY_RE)
    if quality_m is not None:
        limit = _extract_limit(text, defaults.QUALITY_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="exclude_low_quality",
            calls=[RoutedCall("gallery_search", defaults.quality_search_args(limit=limit))],
            select_after_search=wants_select,
        )

    sort_m = _positive_match(text, _SORT_RE)
    if sort_m is not None and not wants_select and _positive_match(text, _DELIVERABLE_RE) is None:
        limit = _extract_limit(text, defaults.SORT_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="sort_overall",
            calls=[RoutedCall("gallery_search", defaults.sort_search_args(limit=limit))],
        )

    if wants_select:
        # "选出20张，但多给我一些全景…" — compound + semantic; do not swallow.
        if _CONTRAST_SEMANTIC_RE.search(text):
            return None
        limit = _extract_limit(text, defaults.SHORTLIST_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="shortlist_select",
            calls=[RoutedCall("gallery_search", defaults.shortlist_search_args(limit=limit))],
            select_after_search=True,
        )

    deliverable_m = _positive_match(text, _DELIVERABLE_RE)
    if deliverable_m is not None:
        if _CONTRAST_SEMANTIC_RE.search(text):
            return None
        limit = _extract_limit(text, defaults.SHORTLIST_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="shortlist_deliverable",
            calls=[RoutedCall("gallery_search", defaults.shortlist_search_args(limit=limit))],
            select_after_search=False,
        )

    return None
