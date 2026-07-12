"""Session-level diversity selection for the gallery ``sort=diverse`` view.

Problem this solves
--------------------
A livehouse scene gets shot many times, so the highest-scoring frames tend to be
near-identical (same framing / light). A pure scalar sort then piles those look-alikes
at the top and the photographer still has to hand-filter. This module groups a session's
frames by *visual similarity*, keeps one **representative** per group (chosen by the
dimensions that actually differ within a burst — peak moment, sharpness, expression),
and folds the rest so the front page shows **coverage** instead of a wall of duplicates.

Design
------
- Similarity signal: CLIP ViT-B-32 cosine (``EmbeddingService``). When ``open-clip-torch``
  is unavailable it **degrades gracefully** to pHash Hamming clustering (same signal the
  Stage2 / gallery-view dedupe already uses), so ``sort=diverse`` always works.
- Greedy single-link clustering seeded best-quality-first, mirroring ``gallery_dedupe``.
- In-cluster representative pick uses *differentiating* VLM dimensions, not ``overall`` —
  within one burst ``composition/light/technical`` are ~constant, so ranking by ``overall``
  is effectively ranking by noise.
- Nothing is deleted: folded frames are returned as ``group_members`` for expand-in-place.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from functools import lru_cache
from typing import Any, Mapping

import numpy as np

logger = logging.getLogger(__name__)

# VLM dims that meaningfully vary *within* a burst (0-10 scale in analysis_results.json).
_DEFAULT_REPRESENTATIVE_DIMS: dict[str, float] = {
    "moment_peak": 0.35,
    "focus_sharpness": 0.25,
    "atmosphere_impact": 0.20,
    "deliverable_subject": 0.20,
}


def diversity_settings(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read ``processing.diversity_selection`` with safe defaults."""
    proc = (config or {}).get("processing") or {}
    if not isinstance(proc, dict):
        proc = {}
    raw = proc.get("diversity_selection")
    if not isinstance(raw, dict):
        raw = {}

    dims = raw.get("representative_dims")
    if not isinstance(dims, dict) or not dims:
        dims = dict(_DEFAULT_REPRESENTATIVE_DIMS)
    else:
        dims = {str(k): float(v) for k, v in dims.items()}

    thr = raw.get("similarity_threshold", 0.90)
    try:
        thr = float(thr)
    except (TypeError, ValueError):
        thr = 0.90

    return {
        "enabled": bool(raw.get("enabled", True)),
        "similarity_threshold": max(0.0, min(1.0, thr)),
        "representative_dims": dims,
        "fallback_phash_hamming": int(raw.get("fallback_phash_hamming", 10) or 10),
        "max_members_returned": int(raw.get("max_members_returned", 40) or 40),
    }


@lru_cache(maxsize=8192)
def _clip_embedding_cached(abs_path: str, mtime_ns: int) -> tuple[float, ...] | None:
    """CLIP embedding for a file, cached by (path, mtime). Returns None when unavailable."""
    _ = mtime_ns
    from services.embedding_service import EmbeddingService

    emb = EmbeddingService.embed_image(abs_path)
    if emb is None:
        return None
    return tuple(float(x) for x in emb.tolist())


def _row_embedding(entry: Mapping[str, Any]) -> np.ndarray | None:
    path = entry.get("path")
    if not path or not isinstance(path, str) or not os.path.isfile(path):
        return None
    abs_path = os.path.abspath(path)
    try:
        mtime_ns = os.stat(abs_path).st_mtime_ns
    except OSError:
        mtime_ns = 0
    vec = _clip_embedding_cached(abs_path, mtime_ns)
    if vec is None:
        return None
    return np.asarray(vec, dtype=np.float32)


def _dimensions(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    dims = entry.get("dimensions")
    return dims if isinstance(dims, dict) else {}


def _representative_score(entry: Mapping[str, Any], dim_weights: Mapping[str, float]) -> float:
    """Weighted differentiating-dimension score (0-100). Falls back to overall when dims missing."""
    dims = _dimensions(entry)
    total_w = 0.0
    acc = 0.0
    for name, w in dim_weights.items():
        v = dims.get(name)
        if v is None:
            continue
        try:
            acc += float(v) * float(w)
            total_w += float(w)
        except (TypeError, ValueError):
            continue
    if total_w <= 0.0:
        scores = entry.get("scores") or {}
        try:
            return float(entry.get("overall_score", scores.get("overall", 0.0)) or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return (acc / total_w) * 10.0  # 0-10 dims -> 0-100


def apply_diversity_selection(
    rows: list[dict],
    settings: Mapping[str, Any],
    *,
    order_key_fn: Callable[[dict], float],
) -> tuple[list[int], dict[int, list[int]], dict[int, int]]:
    """Cluster ``rows`` by visual similarity and pick one representative per cluster.

    Args:
        rows:          gallery rows (need ``path``; ``dimensions`` / ``overall_score`` for ranking).
        settings:      output of :func:`diversity_settings`.
        order_key_fn:  ranking metric for ordering representatives (typically overall score).

    Returns:
        ``(rep_indices_sorted_desc, members_by_rep, group_id_by_rep)`` where ``members_by_rep``
        maps a representative row index to its folded member indices (excluding the rep,
        ordered by differentiating score desc), and ``group_id_by_rep`` maps rep index to a
        stable 1-based group id.
    """
    n = len(rows)
    if n == 0:
        return [], {}, {}

    dim_weights = settings.get("representative_dims") or _DEFAULT_REPRESENTATIVE_DIMS

    # Seed clusters best-quality first so representatives start from strong frames.
    order = sorted(range(n), key=lambda i: order_key_fn(rows[i]), reverse=True)

    from services.embedding_service import EmbeddingService

    use_clip = bool(settings.get("enabled", True)) and EmbeddingService.is_available()

    if use_clip:
        tau = float(settings.get("similarity_threshold", 0.90))
        embeddings: dict[int, np.ndarray | None] = {i: _row_embedding(rows[i]) for i in order}

        def _similar(idx: int, seed: int) -> bool:
            a, b = embeddings.get(idx), embeddings.get(seed)
            if a is None or b is None:
                return False
            return float(np.dot(a, b)) >= tau
    else:
        try:
            from services.gallery_dedupe import resolve_row_phash
            from engine.operators.stage2_prefilter import hamming_64

            max_h = int(settings.get("fallback_phash_hamming", 10))
            phashes: dict[int, int] = {i: resolve_row_phash(rows[i]) for i in order}

            def _similar(idx: int, seed: int) -> bool:
                a, b = phashes.get(idx, 0), phashes.get(seed, 0)
                if not a or not b:
                    return False
                return hamming_64(a, b) <= max_h
        except Exception:
            # Neither CLIP nor pHash available: degrade to no folding (plain quality order).
            logger.warning("diversity_selection: no similarity signal available; returning ungrouped order")

            def _similar(idx: int, seed: int) -> bool:
                return False

    # Greedy single-link clustering against each cluster's seed.
    clusters: list[dict[str, Any]] = []  # {seed: int, members: [int]}
    for idx in order:
        placed = False
        for c in clusters:
            if _similar(idx, c["seed"]):
                c["members"].append(idx)
                placed = True
                break
        if not placed:
            clusters.append({"seed": idx, "members": [idx]})

    rep_indices: list[int] = []
    members_by_rep: dict[int, list[int]] = {}
    for c in clusters:
        members = c["members"]
        rep = max(members, key=lambda i: (_representative_score(rows[i], dim_weights), order_key_fn(rows[i])))
        others = [i for i in members if i != rep]
        others.sort(key=lambda i: _representative_score(rows[i], dim_weights), reverse=True)
        rep_indices.append(rep)
        members_by_rep[rep] = others

    rep_indices.sort(key=lambda i: order_key_fn(rows[i]), reverse=True)
    group_id_by_rep = {rep: gid for gid, rep in enumerate(rep_indices, start=1)}
    return rep_indices, members_by_rep, group_id_by_rep
