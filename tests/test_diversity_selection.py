"""Diversity selection: centroid/seed clustering + MMR spread of representatives."""
from __future__ import annotations

import numpy as np

from services.diversity_selector import (
    _cluster_by_affinity,
    _cluster_clip_centroids,
    _mmr_order_reps,
    diversity_settings,
)


def test_diversity_settings_default_threshold_stricter():
    s = diversity_settings({"processing": {"diversity_selection": {}}})
    assert s["similarity_threshold"] == 0.92
    assert s["max_cluster_size"] == 20
    assert s["fallback_phash_hamming"] == 8
    assert s["mmr_lambda"] == 0.65


def test_cluster_by_affinity_respects_max_size():
    # Everyone is mutually similar; without a cap this would be one giant group.
    def aff(_idx: int, _members: list[int]) -> float:
        return 1.0

    clusters = _cluster_by_affinity(
        list(range(45)),
        affinity_fn=aff,
        threshold=0.9,
        max_cluster_size=20,
    )
    assert all(1 <= len(c) <= 20 for c in clusters)
    assert sum(len(c) for c in clusters) == 45
    assert len(clusters) == 3  # 20 + 20 + 5


def test_cluster_clip_centroids_does_not_single_link_chain():
    # A–B close, B–C close, A–C far: single-link would merge A+B+C; centroid should keep C out.
    def _unit(x: float, y: float) -> np.ndarray:
        v = np.asarray([x, y], dtype=np.float32)
        return v / float(np.linalg.norm(v))

    emb = {
        0: _unit(1.0, 0.0),
        1: _unit(0.96, 0.28),  # ~0.96 with A
        2: _unit(0.28, 0.96),  # close to B-ish direction but far from A centroid after A+B
    }
    # Force order A, B, C
    clusters = _cluster_clip_centroids(
        [0, 1, 2],
        emb,
        tau=0.92,
        max_cluster_size=20,
    )
    # A+B together; C alone (or at least not with both)
    sizes = sorted(len(c) for c in clusters)
    assert sizes == [1, 2]
    assert any(set(c) == {0, 1} for c in clusters)


def test_mmr_spreads_near_duplicate_high_scorers():
    # A,B near-identical high scores; C different lower score.
    # Pure score order → A,B,C. MMR should prefer A then C before B.
    scores = {0: 95.0, 1: 94.0, 2: 80.0}
    sim = {
        (0, 1): 0.98,
        (1, 0): 0.98,
        (0, 2): 0.20,
        (2, 0): 0.20,
        (1, 2): 0.22,
        (2, 1): 0.22,
    }

    def sim_fn(a: int, b: int) -> float:
        return float(sim.get((a, b), 0.0))

    ordered = _mmr_order_reps(
        [0, 1, 2],
        score_fn=lambda i: scores[i],
        sim_fn=sim_fn,
        mmr_lambda=0.55,
    )
    assert ordered[0] == 0
    assert ordered[1] == 2
    assert ordered[2] == 1
