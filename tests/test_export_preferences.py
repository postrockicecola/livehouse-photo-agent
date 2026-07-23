"""Preference pair exporter builds keep/reject pairs from labels."""
from __future__ import annotations

from scripts.eval.export_preferences import build_pairs_from_labels


def test_build_pairs_from_keep_reject():
    rows = [
        {"file": "a.jpg", "overall": 90, "keep": True},
        {"file": "b.jpg", "overall": 80, "keep": True},
        {"file": "c.jpg", "overall": 40, "keep": False},
        {"file": "d.jpg", "overall": 30, "keep": False},
    ]
    pairs = build_pairs_from_labels(rows, max_pairs=3, seed=1)
    assert len(pairs) == 3
    assert all(p["chosen"]["keep"] is True for p in pairs)
    assert all(p["rejected"]["keep"] is False for p in pairs)
