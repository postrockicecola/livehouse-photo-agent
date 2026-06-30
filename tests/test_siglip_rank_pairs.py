"""Pair construction for SigLIP rank training."""
from __future__ import annotations

from scripts.eval.siglip_scorer import build_rank_pairs


def test_same_session_pairs_respect_score_gap() -> None:
    rows = [
        {"file": "a.jpg", "overall": 80.0, "session": "s1", "path": ""},
        {"file": "b.jpg", "overall": 70.0, "session": "s1", "path": ""},
        {"file": "c.jpg", "overall": 75.0, "session": "s1", "path": ""},
    ]
    pairs = build_rank_pairs(rows, min_score_diff=8.0, same_session_only=True)
    assert (0, 1) in pairs  # 80 vs 70
    assert (0, 2) not in pairs  # gap 5
    assert all(rows[w]["overall"] >= rows[l]["overall"] + 8 for w, l in pairs)


def test_cross_session_pair_when_disabled() -> None:
    rows = [
        {"file": "a.jpg", "overall": 90.0, "session": "s1", "path": ""},
        {"file": "b.jpg", "overall": 10.0, "session": "s2", "path": ""},
    ]
    assert build_rank_pairs(rows, min_score_diff=8.0, same_session_only=True) == []
    pairs = build_rank_pairs(rows, min_score_diff=8.0, same_session_only=False)
    assert pairs == [(0, 1)]
