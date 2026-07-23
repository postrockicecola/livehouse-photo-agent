"""Multimodal RAG helpers for the Gallery copilot.

Combines:
- **Text retrieval** over VLM tags / captions / reasons (synonym-aware), and
- **Visual retrieval** via CLIP text→image similarity when ``open-clip`` is available.

Results always carry citation fields so the agent can ground answers in real files.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

TextScorer = Callable[[str, list[str]], int]


def _normalize_scores(raw: dict[str, float]) -> dict[str, float]:
    if not raw:
        return {}
    lo = min(raw.values())
    hi = max(raw.values())
    if hi <= lo:
        return {k: 1.0 for k in raw}
    return {k: (v - lo) / (hi - lo) for k, v in raw.items()}


def visual_scores_for_query(
    query: str,
    files: list[str],
    base_dir: str | Path,
    *,
    top_k: int = 50,
    negative_query: str | None = None,
) -> dict[str, float]:
    """CLIP text→image scores keyed by basename.

    When *negative_query* is set, returns contrast scores ``sim(pos) − sim(neg)``
    over the full candidate corpus (needed for framing intents like 全景 vs 特写).
    """
    q = (query or "").strip()
    if not q or not files:
        return {}
    try:
        from services.embedding_service import EmbeddingService
    except Exception:
        return {}
    if not EmbeddingService.is_available():
        return {}

    paths = [Path(base_dir) / f for f in files]
    cache_dir = Path(base_dir) / ".luma_clip_cache"
    neg = (negative_query or "").strip() or None
    # Contrast needs every candidate; otherwise top-k on pos alone can miss true wides.
    k = len(paths) if neg else min(top_k, len(paths))
    try:
        hits = EmbeddingService.find_similar_to_text(
            q, paths, top_k=k, cache_dir=cache_dir,
        )
    except Exception as exc:
        logger.info("visual RAG skipped: %s", exc)
        return {}
    pos = {h["file_name"]: float(h["similarity"]) for h in hits if h.get("file_name")}
    if not neg:
        return pos
    try:
        neg_hits = EmbeddingService.find_similar_to_text(
            neg, paths, top_k=len(paths), cache_dir=cache_dir,
        )
    except Exception as exc:
        logger.info("visual RAG negative prompt skipped: %s", exc)
        return pos
    neg_map = {h["file_name"]: float(h["similarity"]) for h in neg_hits if h.get("file_name")}
    keys = set(pos) | set(neg_map)
    return {fn: float(pos.get(fn, 0.0) - neg_map.get(fn, 0.0)) for fn in keys}


def hybrid_retrieve(
    rows: list[dict[str, Any]],
    *,
    query: str,
    query_terms: list[str],
    base_dir: str | Path,
    text_hit_score: TextScorer,
    text_blob: Callable[[dict[str, Any]], str],
    mode: str = "hybrid",
    visual_weight: float = 0.45,
    limit: int = 20,
    negative_query: str | None = None,
    framing_intent: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Rank *rows* and return ``(ranked_rows, citations, rag_meta)``.

    ``mode``: ``text`` | ``visual`` | ``hybrid`` (default). Visual falls back to text
    when CLIP is unavailable; ``visual``-only with no CLIP returns empty.

    When *negative_query* is set (framing intents), visual scores are contrast deltas
    and visual-only hits need a clearer margin so close-ups cannot win a 全景 query.
    """
    mode = (mode or "hybrid").strip().lower()
    if mode not in ("text", "visual", "hybrid"):
        mode = "hybrid"
    visual_weight = max(0.0, min(1.0, float(visual_weight)))
    limit = max(1, min(100, int(limit)))
    neg_q = (negative_query or "").strip() or None
    use_contrast = bool(neg_q)

    files = [str(r.get("file") or "") for r in rows if r.get("file")]
    text_raw: dict[str, float] = {}
    for r in rows:
        fn = str(r.get("file") or "")
        if not fn:
            continue
        blob = text_blob(r)
        text_raw[fn] = float(text_hit_score(blob, query_terms) if query_terms else 1.0)

    visual_raw: dict[str, float] = {}
    if mode in ("visual", "hybrid") and (query or "").strip():
        visual_raw = visual_scores_for_query(
            query,
            files,
            base_dir,
            top_k=max(limit * 3, 30),
            negative_query=neg_q,
        )

    text_n = _normalize_scores({k: v for k, v in text_raw.items() if v > 0})
    # For contrast deltas, only normalize among positive-margin candidates.
    visual_for_norm = {k: v for k, v in visual_raw.items() if (v > 0 if use_contrast else True)}
    visual_n = _normalize_scores(visual_for_norm)
    # Cosine floor for plain CLIP; contrast margin for framing (pos − neg).
    _MIN_VISUAL_ABS = 0.05 if use_contrast else 0.22
    if framing_intent in ("wide", "closeup"):
        _MIN_VISUAL_ABS = max(_MIN_VISUAL_ABS, 0.06)

    use_visual = bool(visual_n) and mode in ("visual", "hybrid")
    use_text = mode in ("text", "hybrid") or not use_visual

    if mode == "visual" and not use_visual:
        return [], [], {
            "mode": mode,
            "visual_available": False,
            "fused": False,
            "reason": "CLIP unavailable or no embeddings",
        }

    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    for r in rows:
        fn = str(r.get("file") or "")
        if not fn:
            continue
        t = text_n.get(fn, 0.0) if use_text else 0.0
        v = visual_n.get(fn, 0.0) if use_visual else 0.0
        v_abs = float(visual_raw.get(fn, 0.0)) if use_visual else 0.0
        if mode == "text" and t <= 0 and query_terms:
            continue
        if mode == "visual" and (v <= 0 or v_abs < _MIN_VISUAL_ABS):
            continue
        if mode == "hybrid":
            # Visual-only admission needs a minimum absolute / contrast score.
            visual_ok = v > 0 and v_abs >= _MIN_VISUAL_ABS
            if query_terms and t <= 0 and not visual_ok:
                continue
            if not query_terms:
                fused = v if use_visual else 1.0
            elif use_visual:
                fused = (1.0 - visual_weight) * t + visual_weight * v
                # Keep pure text hits that CLIP missed (tags/captions still count).
                if t > 0 and v <= 0:
                    fused = max(fused, t * 0.85)
                elif t <= 0 and visual_ok:
                    fused = visual_weight * v
            else:
                fused = t
        elif mode == "text":
            fused = t
        else:
            fused = v

        cite = {
            "file": fn,
            "text_score": round(t, 4),
            "visual_score": round(v, 4) if use_visual else None,
            "visual_score_raw": round(v_abs, 4) if use_visual else None,
            "fused_score": round(float(fused), 4),
            "caption": _short_caption(r),
            "tags": list(r.get("tags") or [])[:8],
        }
        scored.append((float(fused), r, cite))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:limit]
    ranked = [r for _, r, _ in top]
    citations = [c for _, _, c in top]
    meta = {
        "mode": mode if use_visual or mode == "text" else "text",
        "requested_mode": mode,
        "visual_available": use_visual,
        "fused": use_visual and use_text and mode == "hybrid",
        "visual_weight": visual_weight if use_visual else 0.0,
        "citation_count": len(citations),
        "contrastive": use_contrast,
        "framing_intent": framing_intent,
        "min_visual_abs": _MIN_VISUAL_ABS,
    }
    return ranked, citations, meta


_BOILERPLATE_CAPTION_PREFIXES = (
    "near-duplicate",
    "stage3 skipped",
    "stage 2",
    "vlm skipped",
    "technical issue:",
)


def _is_boilerplate_caption(text: str) -> bool:
    s = (text or "").strip().lower()
    if not s:
        return True
    return any(s.startswith(p) for p in _BOILERPLATE_CAPTION_PREFIXES)


def _short_caption(row: dict[str, Any]) -> str:
    rb = row.get("reason_bilingual") or {}
    if isinstance(rb, dict):
        cap = rb.get("zh") or rb.get("en")
        if cap and not _is_boilerplate_caption(str(cap)):
            return str(cap)[:160]
    reason = str(row.get("reason") or "")
    if reason and not _is_boilerplate_caption(reason):
        return reason[:160]
    return ""


def format_rag_context(citations: list[dict[str, Any]], *, max_chars: int = 2500) -> str:
    """Compact grounded context block for prompts / forced final answers."""
    lines = ["Retrieved evidence (cite these files; do not invent others):"]
    for i, c in enumerate(citations, 1):
        tags = ", ".join(str(t) for t in (c.get("tags") or [])[:5])
        line = (
            f"[{i}] {c.get('file')} fused={c.get('fused_score')} "
            f"text={c.get('text_score')} visual={c.get('visual_score')} "
            f"tags=[{tags}] caption={c.get('caption') or ''}"
        )
        lines.append(line)
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 20] + "\n…(truncated)"
    return text
