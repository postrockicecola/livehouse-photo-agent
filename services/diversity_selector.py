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
- Centroid / seed link (not single-link-to-any-member): join only when similar to the
  cluster centroid (CLIP) or seed (pHash). Hard ``max_cluster_size`` caps each group at
  a typical burst (~20) so same-stage lighting cannot chain into hundreds of frames.
- In-cluster representative pick uses *differentiating* VLM dimensions, not ``overall``.
- Representatives are then MMR-ordered so the front page spreads scenes instead of
  stacking look-alike high scorers.
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

    # Higher cosine → harder to merge → smaller clusters. Default 0.92 ≈ true near-dups /
    # short bursts; 0.85 previously chained whole stage looks into one giant group.
    thr = raw.get("similarity_threshold", 0.92)
    try:
        thr = float(thr)
    except (TypeError, ValueError):
        thr = 0.92

    mmr_raw = raw.get("mmr_lambda", 0.65)
    try:
        mmr_lambda = float(mmr_raw)
    except (TypeError, ValueError):
        mmr_lambda = 0.65

    try:
        max_cluster_size = int(raw.get("max_cluster_size", 20) or 20)
    except (TypeError, ValueError):
        max_cluster_size = 20
    try:
        max_sync_rows = int(raw.get("max_sync_rows", 1500) or 1500)
    except (TypeError, ValueError):
        max_sync_rows = 1500

    return {
        "enabled": bool(raw.get("enabled", True)),
        # Never run CLIP over an entire gallery inside an HTTP request by default.
        # Use the pHash path unless an operator explicitly accepts that latency.
        "clip_on_demand": bool(raw.get("clip_on_demand", False)),
        "similarity_threshold": max(0.0, min(1.0, thr)),
        "representative_dims": dims,
        # Tighter than gallery_view_dedupe: diversity groups should stay burst-sized.
        "fallback_phash_hamming": int(raw.get("fallback_phash_hamming", 8) or 8),
        "max_members_returned": int(raw.get("max_members_returned", 40) or 40),
        # Hard cap so same-stage CLIP chaining cannot produce 「同款 ×354」.
        "max_cluster_size": max(2, min(64, max_cluster_size)),
        # Gallery routes are synchronous. Avoid quadratic clustering on event-sized sessions.
        "max_sync_rows": max(100, max_sync_rows),
        # Display order of representatives: blend quality vs distance-to-already-shown.
        "mmr_lambda": max(0.0, min(1.0, mmr_lambda)),
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


def _cluster_by_affinity(
    order: list[int],
    *,
    affinity_fn: Callable[[int, list[int]], float],
    threshold: float,
    max_cluster_size: int,
) -> list[list[int]]:
    """Greedy clustering: join the highest-affinity cluster above ``threshold``.

    ``affinity_fn(idx, members)`` returns similarity to that cluster (centroid/seed).
    Unlike single-link-to-any-member, this avoids chaining whole stage looks into one group.
    Full clusters (``>= max_cluster_size``) are skipped so a new burst group can open.
    """
    clusters: list[list[int]] = []
    for idx in order:
        best_i = -1
        best_aff = -1.0
        for i, members in enumerate(clusters):
            if len(members) >= max_cluster_size:
                continue
            aff = float(affinity_fn(idx, members))
            if aff >= threshold and aff > best_aff:
                best_aff = aff
                best_i = i
        if best_i >= 0:
            clusters[best_i].append(idx)
        else:
            clusters.append([idx])
    return clusters


def _cluster_clip_centroids(
    order: list[int],
    embeddings: Mapping[int, np.ndarray | None],
    *,
    tau: float,
    max_cluster_size: int,
) -> list[list[int]]:
    """CLIP clustering against a live L2-normalized mean of each cluster's members."""
    clusters: list[list[int]] = []
    sums: list[np.ndarray | None] = []
    for idx in order:
        vec = embeddings.get(idx)
        best_i = -1
        best_aff = -1.0
        if vec is not None:
            for i, members in enumerate(clusters):
                if len(members) >= max_cluster_size:
                    continue
                acc = sums[i]
                if acc is None:
                    continue
                norm = float(np.linalg.norm(acc))
                if norm <= 1e-8:
                    continue
                aff = float(np.dot(vec, acc / norm))
                if aff >= tau and aff > best_aff:
                    best_aff = aff
                    best_i = i
        if best_i >= 0 and vec is not None:
            clusters[best_i].append(idx)
            acc = sums[best_i]
            sums[best_i] = vec.astype(np.float32, copy=True) if acc is None else acc + vec
        else:
            clusters.append([idx])
            sums.append(vec.astype(np.float32, copy=True) if vec is not None else None)
    return clusters


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

    max_sync_rows = int(settings.get("max_sync_rows", 1500) or 1500)
    if n > max_sync_rows:
        logger.warning(
            "diversity_selection: %d rows exceeds synchronous limit %d; returning score order",
            n,
            max_sync_rows,
        )
        order = sorted(range(n), key=lambda i: order_key_fn(rows[i]), reverse=True)
        return order, {}, {idx: group_id for group_id, idx in enumerate(order, start=1)}

    dim_weights = settings.get("representative_dims") or _DEFAULT_REPRESENTATIVE_DIMS
    max_cluster_size = int(settings.get("max_cluster_size", 20) or 20)

    # Seed clusters best-quality first so representatives start from strong frames.
    order = sorted(range(n), key=lambda i: order_key_fn(rows[i]), reverse=True)

    from services.embedding_service import EmbeddingService

    use_clip = (
        bool(settings.get("enabled", True))
        and bool(settings.get("clip_on_demand", False))
        and EmbeddingService.is_available()
    )
    sim_fn: Callable[[int, int], float] | None = None
    clusters: list[list[int]]

    if use_clip:
        tau = float(settings.get("similarity_threshold", 0.92))
        embeddings: dict[int, np.ndarray | None] = {i: _row_embedding(rows[i]) for i in order}

        def _sim_score(a: int, b: int) -> float:
            va, vb = embeddings.get(a), embeddings.get(b)
            if va is None or vb is None:
                return 0.0
            return float(np.dot(va, vb))

        sim_fn = _sim_score
        clusters = _cluster_clip_centroids(
            order, embeddings, tau=tau, max_cluster_size=max_cluster_size
        )
    else:
        try:
            from services.gallery_dedupe import resolve_row_phash
            from engine.operators.stage2_prefilter import hamming_64

            max_h = int(settings.get("fallback_phash_hamming", 8))
            phashes: dict[int, int] = {i: resolve_row_phash(rows[i]) for i in order}

            def _sim_score(a: int, b: int) -> float:
                pa, pb = phashes.get(a, 0), phashes.get(b, 0)
                if not pa or not pb:
                    return 0.0
                return max(0.0, 1.0 - (hamming_64(pa, pb) / float(max(max_h, 1))))

            def _affinity(idx: int, members: list[int]) -> float:
                # Seed-only: pHash single-link-to-any-member chains stage looks badly.
                if not members:
                    return -1.0
                pa, pb = phashes.get(idx, 0), phashes.get(members[0], 0)
                if not pa or not pb:
                    return -1.0
                return 1.0 if hamming_64(pa, pb) <= max_h else -1.0

            sim_fn = _sim_score
            clusters = _cluster_by_affinity(
                order,
                affinity_fn=_affinity,
                threshold=1.0,
                max_cluster_size=max_cluster_size,
            )
        except Exception:
            logger.warning("diversity_selection: no similarity signal available; returning ungrouped order")
            clusters = [[i] for i in order]
            sim_fn = None

    rep_indices: list[int] = []
    members_by_rep: dict[int, list[int]] = {}
    for members in clusters:
        rep = max(
            members,
            key=lambda i: (_representative_score(rows[i], dim_weights), order_key_fn(rows[i])),
        )
        others = [i for i in members if i != rep]
        others.sort(key=lambda i: _representative_score(rows[i], dim_weights), reverse=True)
        rep_indices.append(rep)
        members_by_rep[rep] = others

    # Spread look-alike *representatives* so the front page is coverage, not a wall of same-scene tops.
    rep_indices = _mmr_order_reps(
        rep_indices,
        score_fn=lambda i: float(order_key_fn(rows[i])),
        sim_fn=sim_fn,
        mmr_lambda=float(settings.get("mmr_lambda", 0.65)),
    )
    group_id_by_rep = {rep: gid for gid, rep in enumerate(rep_indices, start=1)}
    return rep_indices, members_by_rep, group_id_by_rep


def _mmr_order_reps(
    rep_indices: list[int],
    *,
    score_fn: Callable[[int], float],
    sim_fn: Callable[[int, int], float] | None,
    mmr_lambda: float = 0.65,
) -> list[int]:
    """Greedy MMR: next rep maximizes λ·quality − (1−λ)·max_similarity_to_picked."""
    if len(rep_indices) <= 1 or sim_fn is None:
        return sorted(rep_indices, key=score_fn, reverse=True)

    remaining = set(rep_indices)
    picked: list[int] = []
    # First pick: highest score.
    first = max(remaining, key=score_fn)
    picked.append(first)
    remaining.remove(first)

    while remaining:
        def _mmr(i: int) -> float:
            quality = score_fn(i) / 100.0
            max_sim = max((sim_fn(i, j) for j in picked), default=0.0)
            return mmr_lambda * quality - (1.0 - mmr_lambda) * max_sim

        nxt = max(remaining, key=_mmr)
        picked.append(nxt)
        remaining.remove(nxt)
    return picked


def _trailing_burst_num(image_id: str) -> int | None:
    import re

    m = re.search(r"(\d+)(?!.*\d)", image_id)
    return int(m.group(1)) if m else None


def _cluster_map_burst(id_list: list[str], burst_window: int) -> dict[str, int]:
    numbered = sorted(
        ((i, n) for i in id_list if (n := _trailing_burst_num(i)) is not None),
        key=lambda t: (t[1], t[0]),
    )
    out: dict[str, int] = {}
    gid = -1
    prev_num: int | None = None
    for iid, n in numbered:
        if prev_num is None or (n - prev_num) > burst_window:
            gid += 1
        out[iid] = gid
        prev_num = n
    for iid in id_list:
        if iid not in out:
            gid += 1
            out[iid] = gid
    return out


