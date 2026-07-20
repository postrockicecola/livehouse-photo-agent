"""Gallery view near-duplicate folding (pHash), independent of Stage2 VLM dedupe."""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping

import cv2

from engine.operators.stage2_prefilter import hamming_64, image_phash_int, phash_dedup_settings
from services.result_service import _sort_metric

logger = logging.getLogger(__name__)


def gallery_view_dedupe_settings(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """``processing.gallery_view_dedupe`` with fallback to ``stage2_prefilter.phash_near_dup``."""
    proc = (config or {}).get("processing") or {}
    if not isinstance(proc, dict):
        proc = {}
    raw = proc.get("gallery_view_dedupe")
    if not isinstance(raw, dict):
        raw = {}
    stage = phash_dedup_settings(config or {})
    enabled = raw.get("enabled")
    if enabled is None:
        enabled = stage.get("enabled", True)
    max_h = int(raw.get("max_hamming", stage.get("max_hamming", 10)) or 10)
    kpc = int(raw.get("keep_per_cluster", 1) or 1)
    return {
        "enabled": bool(enabled),
        "max_hamming": max(0, min(32, max_h)),
        "keep_per_cluster": max(1, min(20, kpc)),
    }


def row_phash_int(entry: Mapping[str, Any]) -> int:
    ph = entry.get("phash")
    if ph is not None:
        try:
            v = int(ph)
            if v != 0:
                return v
        except (TypeError, ValueError):
            pass
    dbg = entry.get("debug_info")
    if isinstance(dbg, dict):
        try:
            v = int(dbg.get("phash") or 0)
            if v != 0:
                return v
        except (TypeError, ValueError):
            pass
    return 0


@lru_cache(maxsize=8192)
def _phash_from_image_path(path_str: str, mtime_ns: int) -> int:
    _ = mtime_ns
    p = Path(path_str)
    if not p.is_file():
        return 0
    try:
        bgr = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if bgr is None:
            return 0
        h, w = bgr.shape[:2]
        long_edge = max(h, w)
        if long_edge > 512:
            scale = 512.0 / long_edge
            bgr = cv2.resize(
                bgr,
                (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        return image_phash_int(bgr)
    except Exception:
        logger.debug("gallery phash compute failed for %s", p, exc_info=True)
        return 0


def resolve_row_phash(entry: Mapping[str, Any]) -> int:
    ph = row_phash_int(entry)
    if ph:
        return ph
    path = entry.get("path")
    if not path or not isinstance(path, str):
        return 0
    try:
        st = os.stat(path)
        return _phash_from_image_path(os.path.abspath(path), int(st.st_mtime_ns))
    except OSError:
        return _phash_from_image_path(os.path.abspath(path), 0)


def dedupe_row_indices(
    rows: list[dict],
    indices_sorted_desc: list[int],
    *,
    max_hamming: int,
    keep_per_cluster: int,
) -> tuple[list[int], int]:
    """
    Greedy cluster retention on score-sorted indices (best frames first).
    Returns ``(kept_indices, hidden_count)``.
    """
    clusters: list[dict[str, Any]] = []
    kept: list[int] = []
    kpc = max(1, int(keep_per_cluster))

    for idx in indices_sorted_desc:
        row = rows[idx]
        ph = resolve_row_phash(row)
        if ph == 0:
            kept.append(idx)
            continue
        found = -1
        for i, c in enumerate(clusters):
            if hamming_64(ph, int(c["hash"])) <= max_hamming:
                found = i
                break
        if found < 0:
            clusters.append({"hash": ph, "count": 1})
            kept.append(idx)
        else:
            c = clusters[found]
            if int(c["count"]) < kpc:
                c["count"] = int(c["count"]) + 1
                kept.append(idx)

    hidden = len(indices_sorted_desc) - len(kept)
    return kept, hidden


def apply_gallery_view_dedupe(
    rows: list[dict],
    sort: str,
    *,
    settings: Mapping[str, Any],
    sort_key_fn: Callable[[dict], float] | None = None,
) -> tuple[list[int], int, int]:
    """
    Sort all rows by ``sort`` key (desc), dedupe, return ``(kept_indices_in_sort_order, total_kept, total_raw)``.
    """
    total_raw = len(rows)

    def _key(row: dict) -> float:
        if sort_key_fn is not None:
            return float(sort_key_fn(row))
        return _sort_metric(row, sort)

    if not settings.get("enabled", True) or total_raw == 0:
        indices = sorted(range(total_raw), key=lambda i: _key(rows[i]), reverse=True)
        return indices, total_raw, total_raw

    indices_desc = sorted(range(total_raw), key=lambda i: _key(rows[i]), reverse=True)
    kept, _hidden = dedupe_row_indices(
        rows,
        indices_desc,
        max_hamming=int(settings.get("max_hamming", 10)),
        keep_per_cluster=int(settings.get("keep_per_cluster", 5)),
    )
    return kept, len(kept), total_raw
