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
