"""Post-parse validation and sanitation for Stage3 VLM JSON."""
from __future__ import annotations

import logging
from typing import Any

from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)


def _score_spread(dimensions: dict[str, float]) -> float:
    vals = [float(dimensions[k]) for k in STAGE3_DIM_KEYS if k in dimensions]
    if len(vals) < 2:
        return 0.0
    return max(vals) - min(vals)


def sanitize_stage3_parsed(parsed: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize Stage3 parser output for production use.

    - Ensures editing_suggestions empty (Stage4 owns editing guidance).
    - Caps tag count/length; logs compression warnings.
    """
    if not parsed:
        return parsed

    out = dict(parsed)
    out["editing_suggestions"] = []

    tags = out.get("tags") or []
    if isinstance(tags, list):
        cleaned: list[str] = []
        seen: set[str] = set()
        for t in tags:
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
            if len(cleaned) >= 8:
                break
        out["tags"] = cleaned

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
