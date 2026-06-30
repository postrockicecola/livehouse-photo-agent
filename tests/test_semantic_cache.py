"""Tests for the semantic prompt cache (services/cache/semantic_cache).

A deterministic fake embedder (keyword bag-of-words) stands in for a real text encoder,
so these verify exact-hit, similarity-threshold hit/miss, metrics, embed-failure
robustness, and JSON round-trip — no model needed.
"""
from __future__ import annotations

import math

from services.cache.semantic_cache import SemanticCache, cosine_similarity


# A tiny deterministic embedder: counts of a fixed vocabulary → vector.
_VOCAB = ["shutter", "speed", "concert", "iso", "aperture", "lens", "weather", "cooking"]


def _embed(text: str):
    toks = text.lower().split()
    return [float(toks.count(w)) for w in _VOCAB]


def test_cosine_similarity_basics():
    assert cosine_similarity([1, 0], [1, 0]) == 1.0
    assert cosine_similarity([1, 0], [0, 1]) == 0.0
    assert cosine_similarity([], [1]) == 0.0
    assert math.isclose(cosine_similarity([1, 1], [1, 1]), 1.0)


def test_exact_hit_skips_embedding():
    calls = {"n": 0}

    def counting_embed(t):
        calls["n"] += 1
        return _embed(t)

    cache = SemanticCache(counting_embed, min_similarity=0.9)
    cache.store("best shutter speed concert", {"answer": 42})
    before = calls["n"]
    hit = cache.get("best shutter speed concert")  # exact text → no embed call
    assert hit is not None
    assert hit.kind == "exact"
    assert hit.response == {"answer": 42}
    assert calls["n"] == before  # embedding was not invoked on exact hit


def test_similar_hit_within_threshold():
    cache = SemanticCache(_embed, min_similarity=0.8)
    cache.store("shutter speed concert iso", {"a": 1})
    # Reordered / superset of same keywords → high cosine similarity.
    hit = cache.get("concert shutter speed iso aperture")
    assert hit is not None
    assert hit.kind == "similar"
    assert hit.similarity >= 0.8


def test_dissimilar_is_miss():
    cache = SemanticCache(_embed, min_similarity=0.8)
    cache.store("shutter speed concert", {"a": 1})
    assert cache.get("weather cooking lens") is None


def test_metrics_track_hits_and_saves():
    cache = SemanticCache(_embed, min_similarity=0.8)
    cache.store("shutter speed concert", {"a": 1})
    cache.get("shutter speed concert")          # exact hit
    cache.get("concert shutter speed iso")      # similar hit (superset)
    cache.get("weather cooking")                # miss
    m = cache.metrics_dict()
    assert m["semantic_cache_hits_exact"] == 1
    assert m["semantic_cache_hits_similar"] >= 1
    assert m["semantic_cache_saved_inferences"] >= 2
    assert 0.0 < m["semantic_cache_hit_rate"] <= 1.0
    assert m["semantic_cache_entries"] == 1


def test_embed_failure_is_swallowed_on_store_and_get():
    def boom(_):
        raise RuntimeError("embedder down")

    cache = SemanticCache(boom, min_similarity=0.8)
    cache.store("x", {"a": 1})  # store fails silently → nothing cached
    assert cache.metrics_dict()["semantic_cache_entries"] == 0
    # get with an entry present but embed failing → miss, no crash
    cache2 = SemanticCache(_embed, min_similarity=0.8)
    cache2.store("shutter speed", {"a": 1})
    cache2._embed = boom  # type: ignore[attr-defined]
    assert cache2.get("speed shutter concert") is None


def test_empty_text_is_miss_not_crash():
    cache = SemanticCache(_embed)
    assert cache.get("") is None
    cache.store("", {"a": 1})
    assert cache.metrics_dict()["semantic_cache_entries"] == 0


def test_json_round_trip(tmp_path):
    path = tmp_path / "sem.json"
    cache = SemanticCache(_embed, min_similarity=0.8, persist_path=path)
    cache.store("shutter speed concert", {"a": 1})
    cache.save_to_json()

    restored = SemanticCache(_embed, min_similarity=0.8)
    n = restored.load_from_json(path)
    assert n == 1
    hit = restored.get("shutter speed concert")
    assert hit is not None and hit.response == {"a": 1}
