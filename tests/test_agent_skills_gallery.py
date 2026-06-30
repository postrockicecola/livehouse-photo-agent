"""Tests for the read-only Gallery skills (gallery_search / gallery_stats / explain_photo)."""
from __future__ import annotations

import json
from pathlib import Path

from services.agent.skills.gallery import (
    ExplainPhotoSkill,
    GallerySearchSkill,
    GalleryStatsSkill,
    gallery_registry,
)


def _write_results(base: Path, rows: list[dict]) -> None:
    (base / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")


def _sample_rows() -> list[dict]:
    return [
        {
            "file": "a_best.jpg",
            "overall_score": 95.0,
            "scores": {"overall": 95.0, "energy": 9.0, "technical": 8.0, "composition": 9.5},
            "category": "AI_Best_90+",
            "tags": ["crowd", "stage-light"],
            "reason": "Strong peak-action moment.",
        },
        {
            "file": "b_keep.jpg",
            "overall_score": 72.0,
            "scores": {"overall": 72.0, "energy": 7.0, "technical": 6.5, "composition": 7.2},
            "category": "AI_Keep_60-90",
            "tags": ["portrait"],
            "reason_bilingual": {"zh": "构图稳", "en": "Solid framing"},
        },
        {
            "file": "c_trash.jpg",
            "overall_score": 40.0,
            "scores": {"overall": 40.0, "energy": 4.0, "technical": 3.0, "composition": 4.5},
            "category": "AI_Trash_Below60",
            "tags": ["blurry", "crowd"],
            "reason": "Out of focus.",
        },
    ]


def test_registry_has_three_skills(tmp_path: Path) -> None:
    reg = gallery_registry(str(tmp_path))
    assert set(reg.names()) == {"gallery_search", "gallery_stats", "explain_photo"}


def test_search_empty_session(tmp_path: Path) -> None:
    res = GallerySearchSkill(str(tmp_path)).run({})
    assert res.ok is True
    assert res.metadata["count"] == 0


def test_search_min_score_and_sort(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"min_score": 70, "sort_by": "overall"})
    assert res.ok is True
    rows = res.metadata["rows"]
    assert [r["file"] for r in rows] == ["a_best.jpg", "b_keep.jpg"]
    assert res.metadata["count"] == 2


def test_search_tag_and_category_filter(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"tag": "crowd"})
    files = {r["file"] for r in res.metadata["rows"]}
    assert files == {"a_best.jpg", "c_trash.jpg"}

    res2 = GallerySearchSkill(str(tmp_path)).run({"category": "AI_Best_90+"})
    assert [r["file"] for r in res2.metadata["rows"]] == ["a_best.jpg"]


def test_search_limit_clamped(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"limit": 1})
    assert len(res.metadata["rows"]) == 1
    assert res.metadata["count"] == 3  # total matched before limit


def test_stats(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GalleryStatsSkill(str(tmp_path)).run({})
    meta = res.metadata
    assert meta["total"] == 3
    assert meta["score_buckets"] == {"0-60": 1, "60-90": 1, "90-100": 1}
    assert meta["by_category"]["AI_Best_90+"] == 1
    tags = {t["tag"]: t["count"] for t in meta["top_tags"]}
    assert tags["crowd"] == 2


def test_explain_exact_and_substring(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    skill = ExplainPhotoSkill(str(tmp_path))
    res = skill.run({"file": "b_keep.jpg"})
    assert res.ok is True
    assert res.metadata["photo"]["category"] == "AI_Keep_60-90"
    assert res.metadata["photo"]["caption"] == "构图稳"

    res2 = skill.run({"file": "a_best"})
    assert res2.ok is True
    assert res2.metadata["photo"]["file"] == "a_best.jpg"


def test_explain_missing(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = ExplainPhotoSkill(str(tmp_path)).run({"file": "nope.jpg"})
    assert res.ok is False
    assert "nope.jpg" in (res.error or "")


def test_explain_ambiguous(tmp_path: Path) -> None:
    rows = [
        {"file": "show_01.jpg", "overall_score": 80, "scores": {"overall": 80}},
        {"file": "show_02.jpg", "overall_score": 81, "scores": {"overall": 81}},
    ]
    _write_results(tmp_path, rows)
    res = ExplainPhotoSkill(str(tmp_path)).run({"file": "show"})
    assert res.ok is False
    assert res.metadata.get("candidates")
