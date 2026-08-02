"""Deterministic intent → tool routing for Gallery Copilot.

High-frequency requests whose args can be extracted with regex/keywords
(选出 N 张, 连拍去重, 场景配方, 剔糊/过曝) are mapped here and skip the LLM
tool-call round. Semantic / fuzzy intents (吉他手, 胶片风格, …) return ``None``
so the existing JSON-in-text agent loop handles them.

Scene recipes (朋友圈 / 最炸 / 高潮 / 交片) are matched *before* bare shortlist so
filler words like「适合发朋友圈」are not ignored after a plain top-K route.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from services.agent import gallery_search_defaults as defaults

# Shared count-phrase prefixes — used by both limit extraction and shortlist routing
# so "帮我选20张" / "找出15张" stay in sync.
_COUNT_PREFIX = r"(?:选出|挑选|找|找出|给我|帮我(?:找|挑选|选)?|筛选|选)"

_LIMIT_RE = re.compile(rf"(?:{_COUNT_PREFIX})?.{{0,24}}?(\d{{1,3}})\s*张")
# Bare / filler-tolerant select: 选出10张 / 挑选10张 / 选出最炸的10张 / 初选 / 交片
_SELECT_SHORTLIST_RE = re.compile(
    rf"(?:{_COUNT_PREFIX}.{{0,24}}?\d{{1,3}}\s*张|初选|交片)"
)
_SOCIAL_RE = re.compile(
    r"(朋友圈|发朋友圈|社交媒体|适合法发|发\s*ins|发ins|instagram|交片级)",
    re.IGNORECASE,
)
_ENERGY_RE = re.compile(
    r"(最炸|气氛最好|氛围最好|最有气氛|感染力最|energy\s*最高)",
    re.IGNORECASE,
)
_PEAK_RE = re.compile(r"(高潮瞬间|决定性瞬间|最抓拍|瞬间最好|moment\s*peak)", re.IGNORECASE)
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
_PICK_VERB_RE = re.compile(r"(选出|挑选|帮我选|帮我挑|找出|找|给我|初选|标出|交片)")
# Per-photo film recommend (look at analysis) — before keyword vibe apply.
_FILM_RECOMMEND_RE = re.compile(
    r"(最适合这张|最合适这张|适合这张.{0,16}(?:胶片|风格)|"
    r"这张图?.{0,10}最适合.{0,16}(?:胶片|风格)|"
    r"(?:胶片感|胶片风格).{0,6}是什么|"
    r"自动推荐.{0,12}胶片|帮我选.{0,12}胶片|看图.{0,8}(?:推荐|选).{0,8}胶片|"
    r"recommend.{0,16}film|best\s*film\s*(?:look|style|for\s*this))",
    re.IGNORECASE,
)
# Film / grade apply — skip LLM tool JSON (models often claim success without calling).
_FILM_VIBE_RE = re.compile(
    r"(胶片感|复古胶片|复古.{0,8}风格|黑白(?:纪实|风格)|梦核|"
    r"cinestill|portra|kodak\s*portra|fuji\s*classic|"
    r"修成.{0,20}风格|修得.{0,12}(?:狠|重|强)|"
    r"(?:试试|想要|需要).{0,16}(?:复古|胶片|黑白|电影感|暖调).{0,12}风格|"
    r"(?:套|应用|加上).{0,8}(?:胶片|风格)|"
    # Relative intensify — must hit apply_film_vibe (not prose-only LLM).
    r"(?:颜色|色彩|饱和度?).{0,6}(?:再|更).{0,4}(?:浓|重|艳|饱和|狠|强)|"
    r"(?:再|更)(?:浓烈|浓郁|浓|重|狠|强).{0,4}(?:一些|一点|点|些)?|"
    r"(?:胶片感|风格).{0,6}(?:更狠|更重|更强|再浓|更浓)|"
    r"(?:颜色|饱和度?).{0,4}拉满|拉满(?:颜色|饱和))",
    re.IGNORECASE,
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


def _wants_select(text: str) -> bool:
    if _positive_match(text, _SELECT_SHORTLIST_RE) is not None:
        return True
    # Scene + count without a tight prefix (e.g. 适合法发 10 张 + 帮我)
    if _LIMIT_RE.search(text) and _positive_match(text, _PICK_VERB_RE) is not None:
        return True
    return False


def route_gallery_intent(user_text: str) -> Optional[RouteMatch]:
    """Return a :class:`RouteMatch` if ``user_text`` is a deterministic intent.

    Order: dedupe / quality / sort → **scene recipes** → bare shortlist.
    Scene words beat bare top-K so「适合发朋友圈」is not ignored.
    """
    text = (user_text or "").strip()
    if not text:
        return None

    wants_select = _wants_select(text)

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
    if (
        sort_m is not None
        and not wants_select
        and _positive_match(text, _SOCIAL_RE) is None
        and _positive_match(text, _ENERGY_RE) is None
        and _positive_match(text, _PEAK_RE) is None
        and _positive_match(text, re.compile(r"交片")) is None
    ):
        limit = _extract_limit(text, defaults.SORT_DEFAULT_LIMIT)
        return RouteMatch(
            rule_id="sort_overall",
            calls=[RoutedCall("gallery_search", defaults.sort_search_args(limit=limit))],
        )

    if _CONTRAST_SEMANTIC_RE.search(text) and wants_select:
        # "选出20张，但多给我一些全景…" — compound + semantic; do not swallow.
        return None

    limit = _extract_limit(text, defaults.SHORTLIST_DEFAULT_LIMIT)

    pick_verb = _positive_match(text, _PICK_VERB_RE) is not None

    social_m = _positive_match(text, _SOCIAL_RE)
    if social_m is not None:
        return RouteMatch(
            rule_id="shortlist_social",
            calls=[RoutedCall("gallery_search", defaults.social_search_args(limit=limit))],
            # 「帮我找出适合法发…」may use 十张 (no arabic digits) — still select.
            select_after_search=wants_select or pick_verb,
        )

    energy_m = _positive_match(text, _ENERGY_RE)
    if energy_m is not None:
        return RouteMatch(
            rule_id="shortlist_energy",
            calls=[RoutedCall("gallery_search", defaults.energy_search_args(limit=limit))],
            select_after_search=wants_select or pick_verb or bool(_LIMIT_RE.search(text)),
        )

    peak_m = _positive_match(text, _PEAK_RE)
    if peak_m is not None:
        return RouteMatch(
            rule_id="shortlist_peak",
            calls=[RoutedCall("gallery_search", defaults.peak_search_args(limit=limit))],
            select_after_search=wants_select or pick_verb or bool(_LIMIT_RE.search(text)),
        )

    # 交片 (not 交片级 — that is social) → deliverable dual-floor recipe.
    jiao_m = _positive_match(text, re.compile(r"交片(?!级)"))
    if jiao_m is not None:
        return RouteMatch(
            rule_id="shortlist_deliverable",
            calls=[RoutedCall("gallery_search", defaults.deliverable_search_args(limit=limit))],
            select_after_search=True,
        )

    # Per-photo recommend beats keyword vibe (「修成最适合这张图的胶片感」).
    recommend_m = _positive_match(text, _FILM_RECOMMEND_RE)
    if recommend_m is not None and not wants_select:
        return RouteMatch(
            rule_id="recommend_film_for_photo",
            calls=[RoutedCall("recommend_film_for_photo", {"prompt": text})],
        )

    # Film grade / 胶片感 — must persist session_vibe (do not leave to prose-only LLM).
    # Keep behind shortlist verbs so「选出10张复古风格」still shortlists first.
    film_m = _positive_match(text, _FILM_VIBE_RE)
    if film_m is not None and not wants_select:
        return RouteMatch(
            rule_id="apply_film_vibe",
            calls=[RoutedCall("apply_film_vibe", {"prompt": text})],
        )

    if wants_select:
        return RouteMatch(
            rule_id="shortlist_select",
            calls=[RoutedCall("gallery_search", defaults.shortlist_search_args(limit=limit))],
            select_after_search=True,
        )

    return None
