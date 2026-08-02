"""Post-parse validation and sanitation for Stage3 VLM JSON."""
from __future__ import annotations

import logging
from typing import Any

from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)

_MAX_SCENE_TAGS = 8
_MAX_MOOD_TAGS = 4
_MAX_MERGED_TAGS = 12


def _score_spread(dimensions: dict[str, float]) -> float:
    vals = [float(dimensions[k]) for k in STAGE3_DIM_KEYS if k in dimensions]
    if len(vals) < 2:
        return 0.0
    return max(vals) - min(vals)


def _normalize_tag_list(raw: Any, *, max_items: int) -> list[str]:
    """Dedupe (case-insensitive), strip, length-cap; preserve first-seen order."""
    if not isinstance(raw, list) or max_items <= 0:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if not isinstance(t, str):
            continue
        s = t.strip()[:80]
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(s)
        if len(cleaned) >= max_items:
            break
    return cleaned


def sanitize_stage3_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Stage3 parser output for production use.

    - Ensures editing_suggestions empty (Stage4 owns editing guidance).
    - Caps tag / mood_tag count and length; merges mood_tags into tags so
      gallery keyword search (tags blob) can match atmosphere queries.
    - Logs compression warnings.
    """
    if not parsed:
        return parsed

    out = dict(parsed)
    out["editing_suggestions"] = []

    scene = _normalize_tag_list(out.get("tags"), max_items=_MAX_SCENE_TAGS)
    mood = _normalize_tag_list(out.get("mood_tags"), max_items=_MAX_MOOD_TAGS)
    scene_keys = {t.lower() for t in scene}
    mood_only = [t for t in mood if t.lower() not in scene_keys]
    out["mood_tags"] = mood_only

    merged = list(scene)
    seen = set(scene_keys)
    for t in mood_only:
        if len(merged) >= _MAX_MERGED_TAGS:
            break
        key = t.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(t)
    out["tags"] = merged

    dims = out.get("dimensions") or {}
    if isinstance(dims, dict) and dims:
        spread = _score_spread(dims)
        if spread < 0.35:
            logger.debug("Stage3 score compression suspected (spread=%.2f)", spread)

    return out


def classify_parse_failure(*, clean_json: str, raw_text: str | None) -> str:
    """Lightweight retry classifier for observability."""
    text = (clean_json or "").strip()
    if not text:
        return "empty"
    if not text.startswith("{"):
        return "leading_non_json"
    if text.count("{") != text.count("}"):
        return "unbalanced_braces"
    if "```" in (raw_text or ""):
        return "markdown_fence"
    return "json_decode"
