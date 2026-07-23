"""Hybrid RAG helpers (text fusion + optional CLIP)."""
from __future__ import annotations

from services.agent.rag import format_rag_context, hybrid_retrieve


def _rows():
    return [
        {
            "file": "drum.jpg",
            "tags": ["drummer"],
            "reason": "鼓手特写",
            "overall_score": 80,
        },
        {
            "file": "guitar.jpg",
            "tags": ["guitarist"],
            "reason": "吉他手",
            "overall_score": 90,
        },
    ]


def test_hybrid_text_mode_ranks_and_cites(tmp_path):
    def hit(blob: str, terms: list[str]) -> int:
        return sum(1 for t in terms if t in blob)

    def blob(row):
        return f"{' '.join(row.get('tags') or [])} {row.get('reason') or ''}".lower()

    ranked, citations, meta = hybrid_retrieve(
        _rows(),
        query="鼓手",
        query_terms=["鼓手", "drummer"],
        base_dir=tmp_path,
        text_hit_score=hit,
        text_blob=blob,
        mode="text",
        limit=5,
    )
    assert [r["file"] for r in ranked] == ["drum.jpg"]
    assert citations and citations[0]["file"] == "drum.jpg"
    assert meta["visual_available"] is False
    ctx = format_rag_context(citations)
    assert "drum.jpg" in ctx


def test_visual_mode_without_clip_returns_empty(tmp_path, monkeypatch):
    from services import embedding_service as es

    monkeypatch.setattr(es.EmbeddingService, "is_available", classmethod(lambda cls: False))

    ranked, citations, meta = hybrid_retrieve(
        _rows(),
        query="drums",
        query_terms=["drums"],
        base_dir=tmp_path,
        text_hit_score=lambda b, t: 1,
        text_blob=lambda r: "x",
        mode="visual",
        limit=5,
    )
    assert ranked == []
    assert citations == []
    assert meta["visual_available"] is False


def test_hybrid_contrast_filters_negative_margin(tmp_path, monkeypatch):
    """Wide-shot contrast must drop close-ups with negative pos−neg deltas."""
    from services import embedding_service as es
    from services.agent import rag as rag_mod

    monkeypatch.setattr(es.EmbeddingService, "is_available", classmethod(lambda cls: True))

    def _fake_visual(query, files, base_dir, *, top_k=50, negative_query=None):
        # wide panorama ≫ close-up guitar
        assert negative_query
        return {"drum.jpg": 0.14, "guitar.jpg": -0.04}

    monkeypatch.setattr(rag_mod, "visual_scores_for_query", _fake_visual)

    ranked, citations, meta = hybrid_retrieve(
        _rows(),
        query="wide establishing shot",
        query_terms=["全景", "wide shot"],
        base_dir=tmp_path,
        text_hit_score=lambda b, t: 0,
        text_blob=lambda r: "",
        mode="hybrid",
        limit=5,
        negative_query="tight close-up",
        framing_intent="wide",
    )
    assert meta.get("contrastive") is True
    assert [r["file"] for r in ranked] == ["drum.jpg"]
    assert citations[0]["visual_score_raw"] == 0.14
