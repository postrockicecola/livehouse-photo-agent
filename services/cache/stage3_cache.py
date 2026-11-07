"""Stage3 result cache keyed by SHA-256 of file content and scoring context.

The historical class name and metric names remain compatible with callers. Perceptual
hashes are only suitable for grouping; they must never authorize reuse of a score.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Mapping

from utils.json_safe import json_safe

logger = logging.getLogger(__name__)

CACHE_JSON_VERSION = 4
CACHE_HASH_ALGO = "sha256.content+scoring-v3"

# Ephemeral key attached to returned dicts; stripped on store.
CACHE_HIT_META_KEY = "__stage3_cache_meta__"


def stage3_content_key(
    image_path: str,
    context: Mapping[str, Any],
    *,
    content_digest: str | None = None,
) -> str | None:
    """Key by image identity + scoring context.

    ``content_digest`` (hex of the decoded-pixel SHA-256 computed once in Stage2) is
    reused when available to avoid re-reading the file per candidate; identical decoded
    pixels are the same image for a visual VLM score. Falls back to streaming the file
    bytes (fail closed on unreadable files). Only perceptual hashes are unsafe for reuse.
    """
    digest = hashlib.sha256()
    if content_digest:
        digest.update(b"pixels:" + str(content_digest).encode())
    else:
        try:
            with open(image_path, "rb") as image_file:
                for chunk in iter(lambda: image_file.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            return None
    digest.update(b"\0" + json.dumps(json_safe(context), sort_keys=True, ensure_ascii=False).encode())
    return digest.hexdigest()


def _valid_key(key: Any) -> bool:
    return isinstance(key, str) and len(key) == 64 and all(c in "0123456789abcdef" for c in key)


def _is_storable_result(result: Mapping[str, Any]) -> bool:
    if result.get("error") or result.get("inference_degraded") or result.get("used_fallback_defaults"):
        return False
    meta = result.get("stage3_meta") or {}
    if meta.get("used_fallback_defaults") or meta.get("parse_status") == "fail":
        return False
    try:
        if not math.isfinite(float(result["score"])) or not 0 <= float(result["score"]) <= 100:
            return False
    except (KeyError, ValueError, TypeError):
        return False
    outcome = str(meta.get("outcome") or "")
    if outcome in {"fallback_defaults", "parse_failed", "vlm_error", "degraded_inference"}:
        return False
    return True


class Stage3PHashCache:
    """
    Thread-safe content cache. ``max_hamming`` is retained as an ignored legacy option.
    """

    def __init__(
        self,
        *,
        max_hamming: int = 0,
        persist_path: Path | str | None = None,
        on_persist: Callable[[], None] | None = None,
        max_entries: int = 4096,
    ) -> None:
        self._max_hamming = 0
        self._max_entries = max(1, int(max_entries))
        self._persist_path = Path(persist_path) if persist_path else None
        self._on_persist = on_persist
        self._lock = threading.Lock()
        # LRU via OrderedDict (move_to_end on hit/store; popitem(last=False) evicts).
        self._by_phash: OrderedDict[str, dict[str, Any]] = OrderedDict()

        self._lookups = 0
        self._hits_exact = 0
        self._hits_similar = 0
        self._misses = 0
        self._saved_inferences = 0
        self._evictions = 0

    @property
    def max_hamming(self) -> int:
        return self._max_hamming

    def get_cached_result(self, key: str | None) -> dict[str, Any] | None:
        """Return a deep copy only on exact content/context match."""
        with self._lock:
            self._lookups += 1
            if not _valid_key(key):
                self._misses += 1
                return None

            if key in self._by_phash:
                self._hits_exact += 1
                self._saved_inferences += 1
                self._by_phash.move_to_end(key)
                out = copy.deepcopy(self._by_phash[key])
                out[CACHE_HIT_META_KEY] = {
                    "kind": "exact",
                    "content_key": key,
                }
                return out

            self._misses += 1
            return None

    def _evict_if_needed_unlocked(self) -> None:
        while len(self._by_phash) > self._max_entries:
            self._by_phash.popitem(last=False)
            self._evictions += 1

    def store_result(self, key: str | None, result: Mapping[str, Any]) -> None:
        """Store only valid content keys and successful results."""
        if not _valid_key(key):
            return
        if not _is_storable_result(result):
            return
        to_store = copy.deepcopy(dict(result))
        to_store.pop(CACHE_HIT_META_KEY, None)
        with self._lock:
            self._by_phash[key] = to_store
            self._by_phash.move_to_end(key)
            self._evict_if_needed_unlocked()

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
                "stage3_vlm_cache_max_entries": self._max_entries,
                "stage3_vlm_cache_evictions": self._evictions,
            }

    def save_to_json(self, path: Path | str | None = None) -> None:
        """Persist content keys and results (uses ``json_safe`` for numpy, etc.)."""
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
        if ha != CACHE_HASH_ALGO or data.get("version") != CACHE_JSON_VERSION:
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
                if _valid_key(ks) and isinstance(val, dict) and _is_storable_result(val):
                    self._by_phash[ks] = val
                    loaded += 1
            self._evict_if_needed_unlocked()
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
    max_h = 0
    max_entries = int(raw.get("max_entries", 4096) or 4096)
    path_raw = raw.get("persist_path")
    persist_path = Path(str(path_raw)) if path_raw else None
    c = Stage3PHashCache(max_hamming=max_h, persist_path=persist_path, max_entries=max_entries)
    if persist_path and raw.get("load_on_start", True) and persist_path.is_file():
        try:
            c.load_from_json(persist_path, merge=True)
        except Exception as e:
            logger.warning("stage3_vlm_cache load failed %s: %s", persist_path, e)
    return c
