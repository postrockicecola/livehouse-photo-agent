"""Unit tests for per-photo film recommendation (analysis → closed film_*)."""
from __future__ import annotations

from services.film_recommend_service import find_row_by_file, recommend_film_for_row
from services.film_render_service import FILM_VARIANT_IDS


def test_recommend_neon_from_tags() -> None:
    row = {
        "file": "neon_shot.jpg",
        "tags": ["neon", "club", "haze"],
        "mood_tags": ["euphoric"],
        "overall_score": 88,
        "dimensions": {"atmosphere_impact": 9.0, "energy": 8.5},
        "reason": "Neon stage haze.",
    }
    d = recommend_film_for_row(row, prompt="最适合这张图的胶片感")
    assert d.matched is True
    assert d.film_variant in FILM_VARIANT_IDS
    assert d.matched_by.startswith("analysis:")
    assert "neon_shot.jpg" in d.reason_zh or "Neon" in d.reason_zh or "霓虹" in d.label_zh or "neon" in d.matched_by


def test_recommend_bw_from_tags() -> None:
    row = {
        "file": "doc.jpg",
        "tags": ["stage"],
        "mood_tags": ["黑白"],
        "reason": "纪实黑白抓拍",
    }
    d = recommend_film_for_row(row)
    assert d.matched is True
    assert d.film_variant == "film_hp5_bw"
    assert d.matched_by == "analysis:bw_doc"


def test_recommend_default_when_no_keywords() -> None:
    row = {"file": "plain.jpg", "tags": [], "reason": ""}
    d = recommend_film_for_row(row)
    assert d.film_variant == "film_livehouse"
    assert d.matched_by == "analysis:default"
    assert d.matched is True


def test_find_row_by_file_basename() -> None:
    rows = [{"file": "a.jpg"}, {"file": "/tmp/b.jpg"}]
    assert find_row_by_file(rows, "a.jpg")["file"] == "a.jpg"
    assert find_row_by_file(rows, "b.jpg")["file"] == "/tmp/b.jpg"
    assert find_row_by_file(rows, "missing.jpg") is None
