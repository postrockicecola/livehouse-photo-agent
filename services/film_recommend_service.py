"""Per-photo film recommendation from existing analysis rows (P0 thin wedge).

Uses tags / mood / caption already in ``analysis_results.json`` against the closed
film catalog — no second orchestration runtime and no mandatory VLM round-trip.
"""
from __future__ import annotations

import re
from typing import Any

from services.film_render_service import FILM_VARIANT_IDS
from services.vibe_film_policy import FilmVibeDecision, _KEYWORDS, _VIBE_RULES, _DEFAULT_VARIANT

_TOKEN_SPLIT = re.compile(r"[\s,;/|]+")


def _haystack_from_row(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("tags", "mood_tags"):
        vals = row.get(key) or []
        if isinstance(vals, list):
            parts.extend(str(v) for v in vals if v)
    for key in ("reason", "caption", "file"):
        v = row.get(key)
        if v:
            parts.append(str(v))
    rb = row.get("reason_bilingual")
    if isinstance(rb, dict):
        for k in ("zh", "en"):
            if rb.get(k):
                parts.append(str(rb[k]))
    return " ".join(parts).lower()


def _dim(row: dict[str, Any], key: str) -> float:
    if key == "overall":
        try:
            return float(row.get("overall_score") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    for container in (row, row.get("scores"), row.get("dimensions")):
        if not isinstance(container, dict) or container.get(key) is None:
            continue
        try:
            return float(container.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
    return 0.0


def _score_catalog(haystack: str, row: dict[str, Any]) -> list[tuple[float, str, str, str, str]]:
    """Return scored catalog hits: (score, variant, label_zh, reason, tag)."""
    scored: list[tuple[float, str, str, str, str]] = []
    tokens = {t for t in _TOKEN_SPLIT.split(haystack) if t}
    for weight, variant_id, label_zh, reason_tpl, tag in _VIBE_RULES:
        if variant_id not in FILM_VARIANT_IDS:
            continue
        kws = _KEYWORDS.get(tag, ())
        hits = sum(1 for kw in kws if kw.lower() in haystack)
        if hits <= 0:
            continue
        score = float(weight * hits)
        # Soft boosts from Stage3-ish dimensions when text already matched.
        if tag.startswith("neon") or tag in ("cinestill", "ultra_vivid"):
            score += min(3.0, _dim(row, "atmosphere_impact") * 0.25)
        if tag in ("portra", "literary_portrait", "black_mist"):
            score += min(2.0, _dim(row, "deliverable_subject") * 0.2)
        if tag in ("bw_doc", "bw_trix") and any(t in tokens for t in ("bw", "b&w", "black", "白", "黑白")):
            score += 4.0
        if tag == "livehouse":
            score += min(2.0, _dim(row, "energy") * 0.15)
        scored.append((score, variant_id, label_zh, reason_tpl, tag))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def recommend_film_for_row(
    row: dict[str, Any],
    *,
    prompt: str = "",
) -> FilmVibeDecision:
    """Pick a closed-set film variant for one analysis row."""
    raw_prompt = (prompt or "").strip() or "最适合这张图的胶片感"
    haystack = _haystack_from_row(row)
    scored = _score_catalog(haystack, row) if haystack.strip() else []

    if scored:
        best_score, variant_id, label_zh, reason_tpl, tag = scored[0]
        alts = [
            {"variant": v, "label_zh": lab, "reason": f"{why}（次选）"}
            for _, v, lab, why, _t in scored[1:3]
        ]
        file_name = str(row.get("file") or "").strip() or "这张"
        reason_zh = (
            f"根据「{file_name}」的标签/说明匹配到{reason_tpl}"
            f"（分析命中分 {best_score:.1f}）"
        )
        intensity = 1.0
        if _dim(row, "atmosphere_impact") >= 8.0 or _dim(row, "energy") >= 8.5:
            intensity = 1.15
        return FilmVibeDecision(
            film_variant=variant_id if variant_id in FILM_VARIANT_IDS else _DEFAULT_VARIANT,
            label_zh=label_zh,
            reason_zh=reason_zh,
            matched_by=f"analysis:{tag}",
            prompt=raw_prompt,
            intensity=intensity,
            matched=True,
        )

    # No keyword hit — still return a session-ready default for livehouse gigs.
    cap = ""
    rb = row.get("reason_bilingual")
    if isinstance(rb, dict):
        cap = str(rb.get("zh") or rb.get("en") or "").strip()
    if not cap:
        cap = str(row.get("reason") or "").strip()
    reason_zh = (
        f"分析文本未命中具体胶片关键词，使用默认现场风格"
        + (f"；画面：{cap[:48]}" if cap else "")
    )
    return FilmVibeDecision(
        film_variant=_DEFAULT_VARIANT,
        label_zh="默认 · Livehouse",
        reason_zh=reason_zh,
        matched_by="analysis:default",
        prompt=raw_prompt,
        intensity=1.0,
        matched=True,
    )


def find_row_by_file(rows: list[dict[str, Any]], file_name: str) -> dict[str, Any] | None:
    """Match analysis row by basename (case-insensitive)."""
    want = (file_name or "").strip()
    if not want:
        return None
    from pathlib import Path

    want_base = Path(want).name.lower()
    for row in rows:
        if not isinstance(row, dict):
            continue
        f = str(row.get("file") or "").strip()
        if not f:
            continue
        if Path(f).name.lower() == want_base or f.lower() == want_base:
            return row
    return None
