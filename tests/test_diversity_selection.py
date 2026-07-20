"""Diversity selection: single-link clustering + MMR spread of representatives."""
from __future__ import annotations

from services.diversity_selector import _mmr_order_reps, diversity_settings


def test_diversity_settings_default_threshold_loosened():
    s = diversity_settings({"processing": {"diversity_selection": {}}})
    assert s["similarity_threshold"] == 0.85
    assert s["mmr_lambda"] == 0.65


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
