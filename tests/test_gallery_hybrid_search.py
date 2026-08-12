"""Hybrid gallery_search merge + why-line enrichment (no CLIP model required)."""
from __future__ import annotations

from services.agent.skills.gallery_common import (
    _matched_query_terms,
    _pick_why,
    hybrid_merge_rows,
)
from services.agent.skills.gallery_search import GallerySearchSkill


def _row(file: str, *, overall: float, atmosphere: float = 0.0, tags: list[str] | None = None) -> dict:
    return {
        "file": file,
        "overall_score": overall,
        "scores": {
            "overall": overall,
            "energy": 7.0,
            "technical": 7.0,
            "composition": 7.0,
            "atmosphere_impact": atmosphere,
        },
        "energy": 7.0,
        "technical": 7.0,
        "composition": 7.0,
        "atmosphere_impact": atmosphere,
        "category": "keep",
        "semantic_gate": {"status": "pass", "mode": "observe"},
        "tags": tags or [],
        "reason": "ok",
    }


def test_hybrid_merge_prefers_text_hit_then_clip_and_scores() -> None:
    rows = [
        _row("a.jpg", overall=80, atmosphere=9.0, tags=["guitarist"]),
        _row("b.jpg", overall=95, atmosphere=5.0, tags=["crowd"]),
        _row("c.jpg", overall=70, atmosphere=8.5, tags=["stage"]),
    ]
    text_hits = [rows[0]]
    text_scores = {"a.jpg": 4}
    clip_sims = {"a.jpg": 0.30, "c.jpg": 0.45, "b.jpg": 0.10}
    merged = hybrid_merge_rows(
        rows=rows,
        text_hits=text_hits,
        text_scores=text_scores,
        clip_sims=clip_sims,
        pool_files={"a.jpg", "b.jpg", "c.jpg"},
        sort_by="atmosphere_impact",
        min_sim=0.22,
    )
    files = [r["file"] for r in merged]
    # b.jpg below min_sim → excluded; a (text+clip) and c (clip) remain.
    assert files[0] == "a.jpg"
    assert "c.jpg" in files
    assert "b.jpg" not in files


def test_hybrid_merge_blocks_clip_only_for_concrete_subject_query() -> None:
    rows = [
        _row("guitar.jpg", overall=82, tags=["guitarist"]),
        _row("confetti.jpg", overall=90, tags=["confetti", "singer"]),
    ]
    merged = hybrid_merge_rows(
        rows=rows,
        text_hits=[rows[0]],
        text_scores={"guitar.jpg": 3},
        clip_sims={"guitar.jpg": 0.35, "confetti.jpg": 0.48},
        pool_files={"guitar.jpg", "confetti.jpg"},
        sort_by="overall",
        min_sim=0.22,
        allow_clip_only=False,
    )
    assert [row["file"] for row in merged] == ["guitar.jpg"]


def test_pick_why_includes_clip_and_matched_terms() -> None:
    row = _row("g.jpg", overall=88, atmosphere=8.0, tags=["吉他手", "stage"])
    terms = _matched_query_terms(row, ["吉他手", "guitarist", "lonely"])
    assert "吉他手" in terms
    why = _pick_why(
        row,
        sort_by="atmosphere_impact",
        recipe="energy+hybrid",
        clip_sim=0.41,
        matched_terms=terms,
    )
    assert "atmosphere" in why
    assert "clip 0.41" in why
    assert "吉他手" in why


def test_gallery_search_hybrid_path_with_mocked_clip(tmp_path, monkeypatch) -> None:
    rows = [
        _row("hit.jpg", overall=82, atmosphere=8.0, tags=["guitarist", "solo"]),
        _row("miss.jpg", overall=91, atmosphere=9.0, tags=["crowd"]),
    ]
    (tmp_path / "analysis_results.json").write_text(
        __import__("json").dumps(rows), encoding="utf-8"
    )
    # Touch preview files so clip_rank would see them if called; we mock instead.
    (tmp_path / "hit.jpg").write_bytes(b"x")
    (tmp_path / "miss.jpg").write_bytes(b"x")

    def fake_clip_rank(pool, *, base_dir, query, top_k, min_sim=0.0):
        sims = {"hit.jpg": 0.40, "miss.jpg": 0.35}
        return pool, sims, {"retrieval": "clip_text", "attempted": True, "available": True}

    monkeypatch.setenv("LIVEHOUSE_AGENT_SEMANTIC_HYBRID", "1")
    monkeypatch.setenv("LIVEHOUSE_AGENT_SEMANTIC_FALLBACK", "1")
    monkeypatch.setattr(
        "services.agent.skills.gallery_search.clip_rank_rows",
        fake_clip_rank,
    )

    result = GallerySearchSkill(str(tmp_path)).run(
        {"query": "guitarist", "recipe": "energy", "limit": 5}
    )
    assert result.ok
    assert result.metadata.get("retrieval") in ("hybrid", "text", "clip")
    assert "hit.jpg" in (result.metadata.get("files") or [])
    assert "miss.jpg" not in (result.metadata.get("files") or [])
    why_map = {p["file"]: p["why"] for p in result.metadata.get("pick_reasons") or []}
    assert "hit.jpg" in why_map
    assert "clip" in why_map["hit.jpg"] or "match" in why_map["hit.jpg"]
