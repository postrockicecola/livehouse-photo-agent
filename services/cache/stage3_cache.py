"""
Persisted Stage3 VLM cache: ``imagehash.phash`` 64-bit int keys plus Hamming similarity.

Fingerprints match ``debug_info["phash"]`` from Stage2 (`image_phash_int`). Near-duplicates
reuse when Hamming distance is at or below ``max_hamming`` (default allows distance < 8).

Integration example::

    from services.cache.stage3_cache import Stage3PHashCache, stage3_cache_from_config

    # From YAML (processing.stage3_vlm_cache)
    cache = stage3_cache_from_config(config)

    # Or manual
    cache = Stage3PHashCache(max_hamming=7, persist_path=Path(\"/tmp/stage3_cache.json\"))

    phash = int(debug_info.get(\"phash\") or 0)
    hit = cache.get_cached_result(phash)
    if hit is not None:
        meta = hit.pop(\"__stage3_cache_meta__\", {})
        # attach meta to stage3_meta, skip VLM
        ...

    # After real inference
    cache.store_result(phash, result_dict)
    cache.maybe_persist()

    stats = cache.metrics_dict()
    # hit_rate, saved_inference_count, …
"""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from engine.operators.stage2_prefilter import hamming_64
from utils.json_safe import json_safe

logger = logging.getLogger(__name__)

CACHE_JSON_VERSION = 2
CACHE_HASH_ALGO = "imagehash.phash"

# Ephemeral key attached to returned dicts; stripped on store.
CACHE_HIT_META_KEY = "__stage3_cache_meta__"


def _is_storable_result(result: Mapping[str, Any]) -> bool:
    if bool(result.get("error")):
        return False
    outcome = str((result.get("stage3_meta") or {}).get("outcome") or "")
    if outcome == "fallback_defaults":
        return False
    return True


class Stage3PHashCache:
    """
    Thread-safe cache: exact phash key, plus nearest-neighbor within Hamming threshold.
    """

    def __init__(
        self,
        *,
        max_hamming: int = 7,
        persist_path: Path | str | None = None,
        on_persist: Callable[[], None] | None = None,
    ) -> None:
        self._max_hamming = max(0, int(max_hamming))
        self._persist_path = Path(persist_path) if persist_path else None
        self._on_persist = on_persist
        self._lock = threading.Lock()
        self._by_phash: dict[int, dict[str, Any]] = {}

        self._lookups = 0
        self._hits_exact = 0
        self._hits_similar = 0
        self._misses = 0
        self._saved_inferences = 0

    @property
    def max_hamming(self) -> int:
        return self._max_hamming

    def get_cached_result(self, phash: int) -> dict[str, Any] | None:
        """
        Return a deep copy of a matching Stage3 result, or ``None``.

        On hit, the dict includes ``__stage3_cache_meta__`` with
        ``kind`` (``exact`` | ``similar``), ``matched_phash``, ``hamming``.
        Pop this key before persisting or using as a normal result payload.
        """
        ph = int(phash)
        with self._lock:
            self._lookups += 1
            if ph == 0:
                self._misses += 1
                return None

            if ph in self._by_phash:
                self._hits_exact += 1
                self._saved_inferences += 1
                out = copy.deepcopy(self._by_phash[ph])
                out[CACHE_HIT_META_KEY] = {
                    "kind": "exact",
                    "matched_phash": ph,
                    "hamming": 0,
                }
                return out

            best_p: int | None = None
            best_d = self._max_hamming + 1
            for k in self._by_phash:
                d = hamming_64(ph, k)
                if d < best_d:
                    best_d = d
                    best_p = k

            if best_p is not None and best_d <= self._max_hamming:
                self._hits_similar += 1
                self._saved_inferences += 1
                out = copy.deepcopy(self._by_phash[best_p])
                out[CACHE_HIT_META_KEY] = {
                    "kind": "similar",
                    "matched_phash": int(best_p),
                    "hamming": int(best_d),
                }
                return out

            self._misses += 1
            return None

    def store_result(self, phash: int, result: Mapping[str, Any]) -> None:
        """Store a successful Stage3 dict under ``phash`` (ignored if phash is 0 or result not storable)."""
        ph = int(phash)
        if ph == 0:
            return
        if not _is_storable_result(result):
            return
        to_store = copy.deepcopy(dict(result))
        to_store.pop(CACHE_HIT_META_KEY, None)
        with self._lock:
            self._by_phash[ph] = to_store

    def clear(self) -> None:
        with self._lock:
            self._by_phash.clear()

    def metrics_dict(self) -> dict[str, Any]:
        with self._lock:
            lookups = self._lookups
            hits = self._hits_exact + self._hits_similar
            return {
                "cache_hit_exact": self._hits_exact,
                "cache_hit_similar": self._hits_similar,
                "cache_miss": self._misses,
                "stage3_vlm_cache_lookups": lookups,
                "stage3_vlm_cache_hits_exact": self._hits_exact,
                "stage3_vlm_cache_hits_similar": self._hits_similar,
                "stage3_vlm_cache_misses": self._misses,
                "stage3_vlm_cache_saved_inferences": self._saved_inferences,
                "stage3_vlm_cache_hit_rate": round(hits / max(1, lookups), 6) if lookups else 0.0,
                "stage3_vlm_cache_entries": len(self._by_phash),
            }

    def save_to_json(self, path: Path | str | None = None) -> None:
        """Persist ``{phash: result}`` as JSON (uses ``json_safe`` for numpy, etc.)."""
        p = Path(path) if path else self._persist_path
        if not p:
            return
        with self._lock:
            payload = {str(k): json_safe(v) for k, v in self._by_phash.items()}
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": CACHE_JSON_VERSION,
                    "hash_algo": CACHE_HASH_ALGO,
                    "max_hamming": self._max_hamming,
                    "entries": payload,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        logger.info("stage3_vlm_cache saved %s entries → %s", len(payload), p)

    def load_from_json(self, path: Path | str | None = None, *, merge: bool = True) -> int:
        """Load cache from JSON; returns number of entries loaded."""
        p = Path(path) if path else self._persist_path
        if not p or not p.is_file():
            return 0
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        ha = data.get("hash_algo")
        if ha != CACHE_HASH_ALGO:
            logger.info(
                "stage3_vlm_cache: skipping load from %s (hash_algo=%r expected %s). Delete or migrate file to repopulate.",
                p,
                ha,
                CACHE_HASH_ALGO,
            )
            return 0
        entries = data.get("entries") or {}
        loaded = 0
        with self._lock:
            if not merge:
                self._by_phash.clear()
            for ks, val in entries.items():
                try:
                    k = int(ks)
                except (TypeError, ValueError):
                    continue
                if isinstance(val, dict):
                    self._by_phash[k] = val
                    loaded += 1
        logger.info("stage3_vlm_cache loaded %s entries from %s", loaded, p)
        return loaded

    def maybe_persist(self) -> None:
        if self._persist_path:
            self.save_to_json(self._persist_path)
        if self._on_persist:
            self._on_persist()


def stage3_cache_from_config(config: Mapping[str, Any]) -> Stage3PHashCache | None:
    raw = (config.get("processing") or {}).get("stage3_vlm_cache")
    if not isinstance(raw, dict) or not raw.get("enabled", False):
        return None
    max_h = int(raw.get("max_hamming", 7) or 7)
    path_raw = raw.get("persist_path")
    persist_path = Path(str(path_raw)) if path_raw else None
    c = Stage3PHashCache(max_hamming=max_h, persist_path=persist_path)
    if persist_path and raw.get("load_on_start", True) and persist_path.is_file():
        try:
            c.load_from_json(persist_path, merge=True)
        except Exception as e:
            logger.warning("stage3_vlm_cache load failed %s: %s", persist_path, e)
    return c
