"""Semantic response cache: reuse an LLM result when a *similar* prompt was seen before.

This is the text analog of :class:`~services.cache.stage3_cache.Stage3PHashCache`: that
one reuses VLM output for near-duplicate *images* (Hamming distance over perceptual
hashes); this one reuses LLM output for near-duplicate *prompts* (cosine distance over
embeddings). Both are the same idea — near-neighbor reuse under a similarity threshold —
applied to a different modality, and both report a hit rate / saved-inference count so
the cost savings are measurable.

The embedding function is injected (``EmbedFn = (text) -> vector``) so this module has no
heavy model dependency: tests pass a deterministic fake, and production wires it to a
sentence-transformer, SigLIP text head, or an embeddings endpoint. Exact-text repeats
short-circuit the embedding call entirely.

Integration::

    cache = SemanticCache(embed_fn=my_embedder, min_similarity=0.93)
    hit = cache.get(prompt)
    if hit is not None:
        result = hit.response            # skip the LLM
    else:
        result = call_llm(prompt)
        cache.store(prompt, result)
    stats = cache.metrics_dict()         # hit_rate, saved_inferences, entries
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

logger = logging.getLogger(__name__)

EmbedFn = Callable[[str], Sequence[float]]

SEMANTIC_CACHE_VERSION = 1


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in pure Python; 0.0 for empty / zero / mismatched vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _norm_key(text: str) -> str:
    return hashlib.sha1(text.strip().encode("utf-8")).hexdigest()


@dataclass
class _Entry:
    text: str
    embedding: list[float]
    response: Any


@dataclass
class SemanticHit:
    response: Any
    kind: str          # "exact" | "similar"
    similarity: float
    matched_text: str


class SemanticCache:
    """Thread-safe prompt→response cache with exact + nearest-neighbor (cosine) reuse."""

    def __init__(
        self,
        embed_fn: EmbedFn,
        *,
        min_similarity: float = 0.95,
        persist_path: Path | str | None = None,
    ) -> None:
        self._embed = embed_fn
        self._min_similarity = float(min_similarity)
        self._persist_path = Path(persist_path) if persist_path else None
        self._lock = threading.Lock()
        self._by_key: dict[str, _Entry] = {}

        self._lookups = 0
        self._hits_exact = 0
        self._hits_similar = 0
        self._misses = 0
        self._saved_inferences = 0

    @property
    def min_similarity(self) -> float:
        return self._min_similarity

    def get(self, text: str) -> SemanticHit | None:
        """Return a cached response for an exact or semantically-similar prompt, else None."""
        if not text or not text.strip():
            with self._lock:
                self._lookups += 1
                self._misses += 1
            return None
        key = _norm_key(text)

        with self._lock:
            self._lookups += 1
            exact = self._by_key.get(key)
            if exact is not None:
                self._hits_exact += 1
                self._saved_inferences += 1
                return SemanticHit(copy.deepcopy(exact.response), "exact", 1.0, exact.text)
            entries = list(self._by_key.values())

        # Embedding + similarity scan done outside the lock (embed may be slow / IO).
        if not entries:
            with self._lock:
                self._misses += 1
            return None
        try:
            query_vec = list(self._embed(text))
        except Exception:
            logger.exception("semantic_cache embed failed on lookup")
            with self._lock:
                self._misses += 1
            return None

        best: _Entry | None = None
        best_sim = -1.0
        for e in entries:
            sim = cosine_similarity(query_vec, e.embedding)
            if sim > best_sim:
                best_sim = sim
                best = e

        with self._lock:
            if best is not None and best_sim >= self._min_similarity:
                self._hits_similar += 1
                self._saved_inferences += 1
                return SemanticHit(copy.deepcopy(best.response), "similar", round(best_sim, 6), best.text)
            self._misses += 1
        return None

    def store(self, text: str, response: Any) -> None:
        """Embed and cache a prompt→response pair (no-op for empty text / embed failure)."""
        if not text or not text.strip():
            return
        try:
            vec = [float(x) for x in self._embed(text)]
        except Exception:
            logger.exception("semantic_cache embed failed on store")
            return
        entry = _Entry(text=text, embedding=vec, response=copy.deepcopy(response))
        with self._lock:
            self._by_key[_norm_key(text)] = entry

    def clear(self) -> None:
        with self._lock:
            self._by_key.clear()

    def metrics_dict(self) -> dict[str, Any]:
        with self._lock:
            lookups = self._lookups
            hits = self._hits_exact + self._hits_similar
            return {
                "semantic_cache_lookups": lookups,
                "semantic_cache_hits_exact": self._hits_exact,
                "semantic_cache_hits_similar": self._hits_similar,
                "semantic_cache_misses": self._misses,
                "semantic_cache_saved_inferences": self._saved_inferences,
                "semantic_cache_hit_rate": round(hits / max(1, lookups), 6) if lookups else 0.0,
                "semantic_cache_entries": len(self._by_key),
                "semantic_cache_min_similarity": self._min_similarity,
            }

    def save_to_json(self, path: Path | str | None = None) -> None:
        p = Path(path) if path else self._persist_path
        if not p:
            return
        with self._lock:
            payload = [
                {"text": e.text, "embedding": e.embedding, "response": e.response}
                for e in self._by_key.values()
            ]
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(
                {"version": SEMANTIC_CACHE_VERSION, "min_similarity": self._min_similarity, "entries": payload},
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("semantic_cache saved %s entries → %s", len(payload), p)

    def load_from_json(self, path: Path | str | None = None, *, merge: bool = True) -> int:
        p = Path(path) if path else self._persist_path
        if not p or not p.is_file():
            return 0
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("entries") or []
        loaded = 0
        with self._lock:
            if not merge:
                self._by_key.clear()
            for row in entries:
                text = row.get("text")
                emb = row.get("embedding")
                if not isinstance(text, str) or not isinstance(emb, list):
                    continue
                self._by_key[_norm_key(text)] = _Entry(
                    text=text, embedding=[float(x) for x in emb], response=row.get("response")
                )
                loaded += 1
        logger.info("semantic_cache loaded %s entries from %s", loaded, p)
        return loaded
