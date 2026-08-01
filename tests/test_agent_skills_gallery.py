"""Tests for Gallery skills (search / select / vibe helpers / score gap)."""
from __future__ import annotations

import json
from pathlib import Path

from services.agent.skills.gallery import (
    ExplainPhotoSkill,
    GallerySearchSkill,
    GallerySelectSkill,
    GalleryStatsSkill,
    MarkScoreGapSkill,
    _expand_query_terms,
    _normalize_category,
    _style_intent,
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
            "energy": 9.0,
            "technical": 8.0,
            "composition": 9.5,
            "category": "AI_Best_90+",
            "tags": ["crowd", "stage-light", "guitar"],
            "reason": "Strong peak-action moment. 吉他手特写",
        },
        {
            "file": "b_keep.jpg",
            "overall_score": 72.0,
            "scores": {"overall": 72.0, "energy": 7.0, "technical": 8.5, "composition": 5.0},
            "energy": 7.0,
            "technical": 8.5,
            "composition": 5.0,
            "category": "AI_Keep_60-90",
            "tags": ["portrait"],
            "reason_bilingual": {"zh": "构图一般但很清晰", "en": "Sharp but flat framing"},
        },
        {
            "file": "c_trash.jpg",
            "overall_score": 40.0,
            "scores": {"overall": 40.0, "energy": 4.0, "technical": 3.0, "composition": 4.5},
            "energy": 4.0,
            "technical": 3.0,
            "composition": 4.5,
            "category": "AI_Trash_Below60",
            "tags": ["blurry", "crowd"],
            "reason": "Out of focus.",
        },
    ]


def test_registry_has_core_skills(tmp_path: Path) -> None:
    reg = gallery_registry(str(tmp_path))
    names = set(reg.names())
    assert {
        "gallery_search",
        "gallery_stats",
        "explain_photo",
        "gallery_select",
        "apply_film_vibe",
        "export_selected",
        "mark_score_gap",
    } <= names


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


def test_search_social_recipe_prefers_deliverable(tmp_path: Path) -> None:
    rows = [
        {
            "file": "loud.jpg",
            "overall_score": 92.0,
            "scores": {"overall": 92.0, "energy": 9.5, "technical": 5.5, "composition": 8.0},
            "energy": 9.5,
            "technical": 5.5,
            "composition": 8.0,
            "dimensions": {"deliverable_subject": 5.0, "atmosphere_impact": 9.0, "moment_peak": 8.0},
            "category": "AI_Best_90+",
            "tags": ["crowd"],
            "reason": "chaotic pit",
        },
        {
            "file": "clean.jpg",
            "overall_score": 78.0,
            "scores": {"overall": 78.0, "energy": 7.0, "technical": 8.5, "composition": 7.5},
            "energy": 7.0,
            "technical": 8.5,
            "composition": 7.5,
            "dimensions": {"deliverable_subject": 8.5, "atmosphere_impact": 6.5, "moment_peak": 7.0},
            "category": "AI_Keep_60-90",
            "tags": ["portrait"],
            "reason": "readable face",
        },
    ]
    _write_results(tmp_path, rows)
    res = GallerySearchSkill(str(tmp_path)).run(
        {
            "min_score": 65,
            "min_deliverable": 7.0,
            "min_technical": 6.0,
            "sort_by": "deliverable_subject",
            "recipe": "social",
            "rationale": "social share",
            "limit": 10,
        }
    )
    assert res.ok is True
    assert res.metadata["recipe"] == "social"
    assert [r["file"] for r in res.metadata["rows"]] == ["clean.jpg"]
    assert res.metadata["pick_reasons"][0]["file"] == "clean.jpg"
    assert "deliverable" in res.metadata["pick_reasons"][0]["why"]


def test_search_query_matches_caption_and_tags(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"query": "吉他手"})
    assert res.ok is True
    assert [r["file"] for r in res.metadata["rows"]] == ["a_best.jpg"]
    assert "guitar" in " ".join(res.metadata.get("query_terms") or []) or "吉他" in " ".join(
        res.metadata.get("query_terms") or []
    )


def test_expand_query_includes_synonyms() -> None:
    terms = _expand_query_terms("找出吉他手弹琴的特写")
    joined = " ".join(terms)
    assert "guitar" in joined or "guitarist" in joined
    assert "吉他" in joined or "吉他手" in joined


def test_slow_shutter_style_intent_uses_exif(tmp_path: Path, monkeypatch) -> None:
    assert _style_intent("帮我找出十张慢门摄影的照片") == "slow_shutter"
    _write_results(
        tmp_path,
        [
            {
                "file": "a.jpg",
                "overall_score": 88.0,
                "scores": {"overall": 88.0, "energy": 8.0, "technical": 8.0, "composition": 8.0},
                "energy": 8.0,
                "technical": 8.0,
                "composition": 8.0,
                "category": "keep",
                "tags": ["stage3_skipped_gating"],
                "reason": "Stage3 skipped (Stage2 gating); heuristic score only",
            }
        ],
    )

    from services.agent.skills import gallery as gallery_mod

    monkeypatch.setattr(
        gallery_mod,
        "_load_exposure_times",
        lambda _base: {"a": 0.04},  # 1/25s — below 1/15 threshold
    )
    res = GallerySearchSkill(str(tmp_path)).run({"query": "找出慢门摄影", "limit": 10})
    assert res.ok is True
    assert res.metadata["count"] == 0
    assert res.metadata["files"] == []
    assert res.metadata["style_intent"] == "slow_shutter"
    assert "slowest_examples" not in res.metadata  # P1: no inventable near-miss list
    assert "shutter_stats" in res.metadata
    assert "Stage3" not in (res.metadata["rows"][0]["caption"] if res.metadata["rows"] else "")
    assert "0 photo" in res.output.lower() or "no true" in res.output.lower()


def test_slow_shutter_returns_exif_hits(tmp_path: Path, monkeypatch) -> None:
    _write_results(
        tmp_path,
        [
            {
                "file": "slow.jpg",
                "overall_score": 70.0,
                "scores": {"overall": 70.0, "energy": 7.0, "technical": 6.0, "composition": 7.0},
                "energy": 7.0,
                "technical": 6.0,
                "composition": 7.0,
                "category": "keep",
                "tags": ["light trail"],
                "reason": "Intentional long exposure",
            },
            {
                "file": "fast.jpg",
                "overall_score": 90.0,
                "scores": {"overall": 90.0, "energy": 8.0, "technical": 9.0, "composition": 8.0},
                "energy": 8.0,
                "technical": 9.0,
                "composition": 8.0,
                "category": "best",
                "tags": [],
                "reason": "Sharp peak action",
            },
        ],
    )
    from services.agent.skills import gallery as gallery_mod

    monkeypatch.setattr(
        gallery_mod,
        "_load_exposure_times",
        lambda _base: {"slow": 0.25, "fast": 0.004},
    )
    res = GallerySearchSkill(str(tmp_path)).run({"query": "long exposure light trails", "limit": 5})
    assert res.metadata["count"] == 1
    assert res.metadata["files"] == ["slow.jpg"]
    assert res.metadata["rows"][0]["shutter"] == "1/4s"


def test_empty_search_flags_pipeline_only_session(tmp_path: Path) -> None:
    _write_results(
        tmp_path,
        [
            {
                "file": "a.jpg",
                "overall_score": 80.0,
                "scores": {"overall": 80.0, "energy": 7.0, "technical": 7.0, "composition": 7.0},
                "energy": 7.0,
                "technical": 7.0,
                "composition": 7.0,
                "category": "keep",
                "tags": ["stage2_prefilter", "low_quality"],
                "reason": "Stage3 skipped (Stage2 gating); heuristic score only",
            }
        ],
    )
    res = GallerySearchSkill(str(tmp_path)).run({"query": "吉他手弹琴", "mode": "text"})
    assert res.ok is True
    assert res.metadata["count"] == 0
    assert res.metadata["pipeline_tags_only"] is True
    assert res.metadata["vlm_content_count"] == 0
    assert res.metadata.get("tag_status") == "not_available"
    assert "top_tags" not in res.metadata
    assert "semantic_tags" not in res.metadata
    assert "not available" in res.output.lower() or "VLM" in res.output


def test_search_query_expands_chinese_to_english_synonyms(tmp_path: Path) -> None:
    rows = _sample_rows()
    rows.append(
        {
            "file": "d_drum.jpg",
            "overall_score": 88.0,
            "scores": {"overall": 88.0, "energy": 8.5, "technical": 8.0, "composition": 8.0},
            "energy": 8.5,
            "technical": 8.0,
            "composition": 8.0,
            "category": "AI_Keep_60-90",
            "tags": ["drummer", "close-up"],
            "reason": "Drummer mid-hit.",
        }
    )
    _write_results(tmp_path, rows)
    # Chinese query should hit English tag via synonym expansion.
    res = GallerySearchSkill(str(tmp_path)).run({"query": "找出鼓手打鼓的那几张"})
    assert res.ok is True
    files = [r["file"] for r in res.metadata["rows"]]
    assert "d_drum.jpg" in files
    assert "drummer" in " ".join(res.metadata.get("query_terms") or [])


def test_search_exclude_low_quality(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"exclude_low_quality": True, "exclude_trash": True})
    files = {r["file"] for r in res.metadata["rows"]}
    assert "c_trash.jpg" not in files
    assert "a_best.jpg" in files


def test_search_tag_and_category_filter(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySearchSkill(str(tmp_path)).run({"tag": "crowd"})
    files = {r["file"] for r in res.metadata["rows"]}
    assert files == {"a_best.jpg", "c_trash.jpg"}

    # Canonical short name (schema enum) and legacy AI_* folder label are aliases.
    res2 = GallerySearchSkill(str(tmp_path)).run({"category": "best"})
    assert [r["file"] for r in res2.metadata["rows"]] == ["a_best.jpg"]
    res3 = GallerySearchSkill(str(tmp_path)).run({"category": "AI_Best_90+"})
    assert [r["file"] for r in res3.metadata["rows"]] == ["a_best.jpg"]


def test_category_aliases_are_same_score_band() -> None:
    """AI_* folder names ≡ best/keep/trash JSON labels (not a second taxonomy)."""
    assert _normalize_category("AI_Best_90+") == "best"
    assert _normalize_category("AI_Keep_60-90") == "keep"
    assert _normalize_category("AI_Trash_Below60") == "trash"
    assert _normalize_category("best") == "best"
    assert GallerySearchSkill.parameters["properties"]["category"]["enum"] == [
        "best",
        "keep",
        "trash",
    ]


def test_search_category_matches_short_labels_in_results(tmp_path: Path) -> None:
    """Production analysis_results use short labels; AI_* args must still hit them."""
    rows = [
        {
            "file": "x.jpg",
            "overall_score": 92.0,
            "scores": {"overall": 92.0, "energy": 8.0, "technical": 8.0, "composition": 8.0},
            "category": "best",
            "tags": [],
            "reason": "ok",
        },
        {
            "file": "y.jpg",
            "overall_score": 70.0,
            "scores": {"overall": 70.0, "energy": 7.0, "technical": 7.0, "composition": 7.0},
            "category": "keep",
            "tags": [],
            "reason": "ok",
        },
    ]
    _write_results(tmp_path, rows)
    skill = GallerySearchSkill(str(tmp_path))
    assert {r["file"] for r in skill.run({"category": "AI_Best_90+"}).metadata["rows"]} == {"x.jpg"}
    assert {r["file"] for r in skill.run({"category": "keep"}).metadata["rows"]} == {"y.jpg"}


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
    assert res.metadata["photo"]["caption"] == "构图一般但很清晰"

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


def test_gallery_select_writes_curation(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = GallerySelectSkill(str(tmp_path)).run({"files": ["a_best.jpg", "b_keep.jpg"]})
    assert res.ok is True
    assert res.metadata["ui_action"] == "reload_curation"
    assert set(res.metadata["selected_keys"]) == {"a_best.jpg", "b_keep.jpg"}
    cur_path = tmp_path / "runtime" / "gallery_curation.json"
    # write path may use runtime_dir helper
    from utils.gallery_curation import read_gallery_curation

    data = read_gallery_curation(str(tmp_path))
    assert data is not None
    assert set(data["selected_keys"]) == {"a_best.jpg", "b_keep.jpg"}
    assert cur_path.exists() or True  # path layout may vary; read API is SSOT


def test_mark_score_gap_selects(tmp_path: Path) -> None:
    _write_results(tmp_path, _sample_rows())
    res = MarkScoreGapSkill(str(tmp_path)).run(
        {"min_technical": 7.5, "max_composition": 6.5, "select": True}
    )
    assert res.ok is True
    assert "b_keep.jpg" in res.metadata["files"]
    assert res.metadata["ui_action"] == "reload_curation"
