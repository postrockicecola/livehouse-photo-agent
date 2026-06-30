"""Incremental, atomic updates to ``analysis_results.json`` while the pipeline runs."""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = frozenset({"best", "keep", "trash"})


def _record_key(record: Dict[str, Any]) -> str:
    raw = (record.get("path") or "").strip()
    if raw:
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw
    fn = (record.get("file") or "").strip()
    cat = (record.get("category") or "").strip().lower()
    if fn and cat:
        return f"{cat}::{fn}"
    return fn


def _normalize_category(record: Dict[str, Any]) -> None:
    cat = (record.get("category") or "").strip().lower()
    if cat not in _VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(_VALID_CATEGORIES)}, got {cat!r}")
    record["category"] = cat


class GalleryStreamer:
    """
    Thread-safe merge of single rows into ``<output_dir>/analysis_results.json``.

    Each ``upsert`` loads the current list, replaces an existing row with the same
    stable key (resolved ``path``, or ``category`` + ``file``), or appends.
    Writes via a temp file + replace for readers (and the poller) to see consistent JSON.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)
        self._path = self._output_dir / "analysis_results.json"
        self._lock = threading.Lock()

    @property
    def json_path(self) -> Path:
        return self._path

    def _load(self) -> List[Dict[str, Any]]:
        if not self._path.is_file():
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("gallery_streamer: reset corrupt or unreadable %s (%s)", self._path, e)
            return []
        if not isinstance(data, list):
            return []
        return [x for x in data if isinstance(x, dict)]

    def _atomic_write(self, rows: List[Dict[str, Any]]) -> None:
        self._output_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        tmp.replace(self._path)

    def upsert(self, record: Dict[str, Any]) -> None:
        """Merge one gallery row (must include ``category`` and ``path`` or ``file``)."""
        rec = dict(record)
        _normalize_category(rec)
        key = _record_key(rec)
        if not key:
            raise ValueError("record needs non-empty path or (category + file)")

        with self._lock:
            rows = self._load()
            index_by_key: Dict[str, int] = {}
            for i, row in enumerate(rows):
                k = _record_key(row)
                if k:
                    index_by_key[k] = i
            if key in index_by_key:
                rows[index_by_key[key]] = rec
            else:
                rows.append(rec)
            self._atomic_write(rows)

    def reset(self) -> None:
        """Truncate to an empty list (optional: call at job start)."""
        with self._lock:
            self._atomic_write([])


# -----------------------------------------------------------------------------
# Minimal usage (from pipeline code after an image lands in best/keep/trash):
#
#   from services.gallery.gallery_streamer import GalleryStreamer
#
#   streamer = GalleryStreamer(previews_dir)  # same dir as analysis_results.json
#   streamer.upsert({
#       "file": "IMG_001.jpg",
#       "path": str(previews_dir / "best" / "IMG_001.jpg"),
#       "category": "best",
#       "overall_score": 87.5,
#       "energy": 7.2,
#       "technical": 8.1,
#       "composition": 7.8,
#       "scores": {"overall": 87.5, "energy": 7.2, "technical": 8.1, "composition": 7.8, "laplacian": 120.0},
#       "tags": ["crowd energy"],
#   })
# -----------------------------------------------------------------------------
