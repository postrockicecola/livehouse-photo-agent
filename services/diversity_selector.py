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
- Greedy single-link clustering (similar to *any* cluster member, not only the seed)
  so lighting-drift bursts still collapse into one group.
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

    thr = raw.get("similarity_threshold", 0.85)
    try:
        thr = float(thr)
    except (TypeError, ValueError):
        thr = 0.85

    agent_fin = raw.get("agent_finalize")
    if not isinstance(agent_fin, dict):
        agent_fin = {}
    max_per = agent_fin.get("max_per_cluster", 1)
    try:
        max_per = int(max_per)
    except (TypeError, ValueError):
        max_per = 1

    mmr_raw = raw.get("mmr_lambda", 0.65)
    try:
        mmr_lambda = float(mmr_raw)
    except (TypeError, ValueError):
        mmr_lambda = 0.65

    return {
        "enabled": bool(raw.get("enabled", True)),
        "similarity_threshold": max(0.0, min(1.0, thr)),
        "representative_dims": dims,
        "fallback_phash_hamming": int(raw.get("fallback_phash_hamming", 10) or 10),
        "max_members_returned": int(raw.get("max_members_returned", 40) or 40),
        # Agent finalize / merge: at most N keepers per visual (or burst) cluster.
        "finalize_enabled": bool(agent_fin.get("enabled", True)),
        "max_per_cluster": max(1, max_per),
        "burst_window": max(1, int(agent_fin.get("burst_window", 3) or 3)),
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
    sim_fn: Callable[[int, int], float] | None = None

    if use_clip:
        tau = float(settings.get("similarity_threshold", 0.85))
        embeddings: dict[int, np.ndarray | None] = {i: _row_embedding(rows[i]) for i in order}

        def _similar(idx: int, other: int) -> bool:
            a, b = embeddings.get(idx), embeddings.get(other)
            if a is None or b is None:
                return False
            return float(np.dot(a, b)) >= tau

        def _sim_score(a: int, b: int) -> float:
            va, vb = embeddings.get(a), embeddings.get(b)
            if va is None or vb is None:
                return 0.0
            return float(np.dot(va, vb))

        sim_fn = _sim_score
    else:
        try:
            from services.gallery_dedupe import resolve_row_phash
            from engine.operators.stage2_prefilter import hamming_64

            max_h = int(settings.get("fallback_phash_hamming", 10))
            phashes: dict[int, int] = {i: resolve_row_phash(rows[i]) for i in order}

            def _similar(idx: int, other: int) -> bool:
                a, b = phashes.get(idx, 0), phashes.get(other, 0)
                if not a or not b:
                    return False
                return hamming_64(a, b) <= max_h

            def _sim_score(a: int, b: int) -> float:
                pa, pb = phashes.get(a, 0), phashes.get(b, 0)
                if not pa or not pb:
                    return 0.0
                # Map Hamming 0..max_h → 1..~0 so MMR can still spread near-dups.
                return max(0.0, 1.0 - (hamming_64(pa, pb) / float(max(max_h, 1))))

            sim_fn = _sim_score
        except Exception:
            # Neither CLIP nor pHash available: degrade to no folding (plain quality order).
            logger.warning("diversity_selection: no similarity signal available; returning ungrouped order")

            def _similar(idx: int, other: int) -> bool:
                return False

            sim_fn = None

    # True single-link: join if similar to *any* member (not only the seed).
    # Seed-only compare splits livehouse bursts when lighting drifts along the sequence.
    clusters: list[dict[str, Any]] = []  # {seed: int, members: [int]}
    for idx in order:
        placed = False
        for c in clusters:
            if any(_similar(idx, m) for m in c["members"]):
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


def _cluster_map_visual(
    id_list: list[str],
    items_by_id: Mapping[str, Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> dict[str, int] | None:
    """Cluster by CLIP/pHash when enough on-disk paths exist; else None."""
    rows: list[dict[str, Any]] = []
    for iid in id_list:
        it = items_by_id[iid]
        path = it.get("path")
        rows.append(
            {
                "path": path if isinstance(path, str) else None,
                "overall_score": float(it.get("score") or 0.0),
                "dimensions": it.get("dimensions") if isinstance(it.get("dimensions"), dict) else {},
            }
        )
    if not any(isinstance(r.get("path"), str) and os.path.isfile(r["path"]) for r in rows):
        return None
    if not bool(settings.get("enabled", True)):
        return None

    rep_indices, members_by_rep, _ = apply_diversity_selection(
        rows,
        settings,
        order_key_fn=lambda r: float(r.get("overall_score") or 0.0),
    )
    # If every frame is its own cluster, visual signal may be unavailable — still valid.
    cluster_of: dict[str, int] = {}
    for gid, rep in enumerate(rep_indices):
        cluster_of[id_list[rep]] = gid
        for m in members_by_rep.get(rep, []):
            cluster_of[id_list[m]] = gid
    return cluster_of


def _cluster_map_features(id_list: list[str], items_by_id: Mapping[str, Mapping[str, Any]]) -> dict[str, int] | None:
    if not any(items_by_id[i].get("cluster_id") is not None for i in id_list):
        return None
    remapped: dict[Any, int] = {}
    out: dict[str, int] = {}
    next_gid = 0
    for iid in id_list:
        raw = items_by_id[iid].get("cluster_id")
        if raw is None:
            out[iid] = next_gid
            next_gid += 1
            continue
        if raw not in remapped:
            remapped[raw] = next_gid
            next_gid += 1
        out[iid] = remapped[raw]
    return out


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


def diversify_keeper_ids(
    items: list[Mapping[str, Any]],
    proposed_ids: list[str],
    *,
    target: int,
    settings: Mapping[str, Any] | None = None,
    fill_ids: list[str] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Cap keepers to ``max_per_cluster`` per visual/burst group, then refill to ``target``.

    Each item needs ``id``; optional ``path``, ``score``, ``dimensions``, ``cluster_id``.
    Preference order: ``proposed_ids`` (as given), then ``fill_ids`` by score desc.
    Signal priority: CLIP/pHash (when paths exist) → ``cluster_id`` → filename burst window.
    """
    settings = dict(settings or diversity_settings(None))
    max_per = int(settings.get("max_per_cluster", 1) or 1)
    burst_window = int(settings.get("burst_window", 3) or 3)
    target = max(0, int(target))

    items_by_id: dict[str, Mapping[str, Any]] = {}
    for it in items:
        iid = str(it.get("id") or "")
        if iid:
            items_by_id[iid] = it

    def _known(ids: list[str] | None) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in ids or []:
            iid = str(raw)
            if iid in items_by_id and iid not in seen:
                seen.add(iid)
                out.append(iid)
        return out

    proposed = _known(proposed_ids)
    fill = [i for i in _known(fill_ids) if i not in set(proposed)]
    fill.sort(key=lambda i: float(items_by_id[i].get("score") or 0.0), reverse=True)
    pool = proposed + fill

    if target == 0 or not pool:
        return [], {"dropped": [], "signal": "none", "before": len(proposed), "after": 0}

    cluster_of = _cluster_map_visual(pool, items_by_id, settings)
    signal = "clip_or_phash"
    if cluster_of is None:
        cluster_of = _cluster_map_features(pool, items_by_id)
        signal = "features_cluster_id"
    if cluster_of is None:
        cluster_of = _cluster_map_burst(pool, burst_window)
        signal = "burst_window"

    kept: list[str] = []
    dropped: list[str] = []
    counts: dict[int, int] = {}
    for iid in pool:
        if len(kept) >= target:
            if iid in proposed:
                dropped.append(iid)
            continue
        cid = cluster_of.get(iid, hash(iid) & 0x7FFFFFFF)
        if counts.get(cid, 0) >= max_per:
            if iid in proposed:
                dropped.append(iid)
            continue
        kept.append(iid)
        counts[cid] = counts.get(cid, 0) + 1

    return kept, {
        "signal": signal,
        "before": len(proposed),
        "after": len(kept),
        "dropped": dropped,
        "max_per_cluster": max_per,
        "clusters_used": len(counts),
    }
