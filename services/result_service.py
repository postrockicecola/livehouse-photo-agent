"""Result loading/normalization service for gallery API."""
from __future__ import annotations

import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import cast
from urllib.parse import quote

from services.image_service import read_exif_orientation_tag
from services.jpeg_exif_orientation import sync_gallery_entry_display_dimensions
from services.path_service import PathResolver

logger = logging.getLogger(__name__)


def _results_json_path(base_dir: str) -> str:
    return os.path.join(base_dir, "analysis_results.json")


def load_raw_results(base_dir: str) -> list[dict]:
    results_json = _results_json_path(base_dir)
    if not os.path.exists(results_json):
        return []
    try:
        with open(results_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return data


# Absolute Previews dir -> (scan_mtime_signal, rows). Invalidated when Previews / AI_* mtimes change.
_DISK_ROWS_CACHE: dict[str, tuple[float, list[dict]]] = {}

_DISK_SCAN_FOLDERS = (
    "AI_Best_90+",
    "AI_Keep_60-90",
    "AI_Trash_Below60",
    "best",
    "keep",
    "trash",
)


def _previews_scan_mtime(base: Path) -> float:
    """Max mtime of Previews + classified subfolders (files landing mid-job bump these)."""
    mt = -1.0
    try:
        mt = max(mt, float(os.path.getmtime(base)))
    except OSError:
        pass
    for folder in _DISK_SCAN_FOLDERS:
        d = base / folder
        if not d.is_dir():
            continue
        try:
            mt = max(mt, float(os.path.getmtime(d)))
        except OSError:
            continue
    return mt


def _discover_gallery_rows_from_disk(base_dir: str) -> list[dict]:
    """Build minimal unscored rows from JPEG/PNG on disk (running or completed sessions).

    Scans the same layout as :meth:`services.path_service.PathResolver._iter_preview_roots`
    (classified subfolders, then loose files directly under ``Previews``).
    Cached by directory mtime so mid-job Gallery polls stay cheap.
    """
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []
    base_key = str(base)
    scan_mtime = _previews_scan_mtime(base)
    cached = _DISK_ROWS_CACHE.get(base_key)
    if cached and cached[0] == scan_mtime:
        return cached[1]

    seen: set[str] = set()
    rows: list[dict] = []

    def push_file(f: Path) -> None:
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            return
        name = f.name
        if name.startswith("._"):
            return
        if name in seen:
            return
        seen.add(name)
        rows.append(
            {
                "file": name,
                "path": str(f.resolve()),
                "overall_score": 0.0,
                "scores": {
                    "overall": 0.0,
                    "energy": 0.0,
                    "technical": 0.0,
                    "composition": 0.0,
                    "laplacian": 0.0,
                },
                "analysis_pending": True,
            }
        )

    for folder in _DISK_SCAN_FOLDERS:
        d = base / folder
        if not d.is_dir():
            continue
        try:
            for f in sorted(d.iterdir()):
                if f.is_file():
                    push_file(f)
        except OSError:
            continue

    try:
        for f in sorted(base.iterdir()):
            if f.is_file():
                push_file(f)
    except OSError:
        pass

    _DISK_ROWS_CACHE[base_key] = (scan_mtime, rows)
    return rows


def _row_basename(row: dict) -> str:
    name = str(row.get("file") or "").strip()
    if name:
        return name
    path = str(row.get("path") or "").strip()
    return Path(path).name if path else ""


def merge_json_and_disk_gallery_rows(json_rows: list[dict], disk_rows: list[dict]) -> list[dict]:
    """Union JSON analysis rows with on-disk previews (JSON wins on basename).

    Lets Gallery show every preview in a running session while VLM is still writing
    scores into ``analysis_results.json``.
    """
    if not disk_rows:
        return list(json_rows)
    if not json_rows:
        return list(disk_rows)

    by_name: dict[str, dict] = {}
    order: list[str] = []
    for row in json_rows:
        name = _row_basename(row)
        if not name or name in by_name:
            continue
        by_name[name] = row
        order.append(name)
    for row in disk_rows:
        name = _row_basename(row)
        if not name or name in by_name:
            continue
        by_name[name] = row
        order.append(name)
    return [by_name[n] for n in order]


# Absolute path ``analysis_results.json`` -> (mtime, rows). Rows are **never** mutated once cached.
_JSON_ROWS_CACHE: dict[str, tuple[float, list[dict]]] = {}


def _read_json_rows_cached(abs_json_path: str) -> list[dict]:
    if not os.path.isfile(abs_json_path):
        _JSON_ROWS_CACHE.pop(os.path.abspath(abs_json_path), None)
        return []
    path_abs = os.path.abspath(abs_json_path)
    try:
        mtime = os.path.getmtime(path_abs)
    except OSError:
        return []

    cached = _JSON_ROWS_CACHE.get(path_abs)
    if cached and cached[0] == mtime:
        return cached[1]

    try:
        with open(path_abs, "r", encoding="utf-8") as f:
            payload = cast(object, json.load(f))
    except Exception:
        _JSON_ROWS_CACHE.pop(path_abs, None)
        return []

    if not isinstance(payload, list):
        rows = []
    else:
        rows = [cast(dict, r) for r in payload if isinstance(r, dict)]

    _JSON_ROWS_CACHE[path_abs] = (mtime, rows)
    return rows


def _f(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _sort_metric(entry: dict, sort: str) -> float:
    """Sort key aligned with normalize_scores (+ laplacian) without mutating the row."""
    scores = entry.get("scores") or {}
    if sort == "laplacian":
        return float(scores.get("laplacian", 0) or 0)
    if sort == "overall":
        overall = entry.get("overall_score", scores.get("overall"))
        return max(0.0, min(100.0, _f(overall, 0.0)))
    if sort == "energy":
        return _f(scores.get("energy", entry.get("energy", 0)))
    if sort == "technical":
        return _f(scores.get("technical", entry.get("technical", 0)))
    if sort == "composition":
        return _f(scores.get("composition", entry.get("composition", 0)))
    return 0.0


def normalize_scores(entry: dict) -> dict:
    scores = entry.get("scores") or {}
    entry["energy"] = _f(scores.get("energy", entry.get("energy", 0)))
    entry["technical"] = _f(scores.get("technical", entry.get("technical", 0)))
    entry["composition"] = _f(scores.get("composition", entry.get("composition", 0)))
    overall = entry.get("overall_score", scores.get("overall"))
    entry["overall_score"] = max(0.0, min(100.0, _f(overall, 0.0)))
    return entry


def resolve_paths(entry: dict, base_dir: str) -> dict:
    path = entry.get("path", "")
    if path and not os.path.isabs(path):
        abs_path = os.path.normpath(os.path.join(base_dir, path))
        if os.path.exists(abs_path):
            entry["path"] = abs_path

    path = entry.get("path") or ""
    if path and os.path.isfile(path):
        entry["path"] = os.path.abspath(path)
    else:
        fn = entry.get("file") or (os.path.basename(path) if path else "")
        if fn:
            for sub in ("AI_Best_90+", "AI_Keep_60-90", "AI_Trash_Below60", ""):
                alt = os.path.join(base_dir, sub, fn) if sub else os.path.join(base_dir, fn)
                if os.path.isfile(alt):
                    entry["path"] = os.path.abspath(alt)
                    break

    p = entry.get("path", "")
    file_name = entry.get("file") or (os.path.basename(p) if p else "")
    if file_name:
        source_guess = os.path.join(base_dir, file_name)
        if os.path.isfile(source_guess):
            entry["before_path"] = os.path.abspath(source_guess)
        else:
            entry["before_path"] = p
    entry["path_quoted"] = quote(p, safe="") if p else ""
    bp = entry.get("before_path") or ""
    entry["before_path_quoted"] = quote(bp, safe="") if bp else ""
    return entry


def inject_layout(entry: dict) -> dict:
    path = entry.get("path") or ""
    if not path or not os.path.isfile(path):
        return entry
    try:
        from engine.operators.image_processor import ImageProcessor

        lay = ImageProcessor.get_display_layout(path)
        if lay:
            for k in ("width", "height", "orientation"):
                if k in lay:
                    entry[k] = lay[k]
    except Exception:
        pass
    return entry


def _coerce_raw_orientation_to_degrees(text: str) -> int:
    t = (text or "").lower()
    if "rotate 90 cw" in t:
        return 90
    if "rotate 270 cw" in t or "rotate 90 ccw" in t:
        return -90
    if "rotate 180" in t:
        return 180
    return 0


@lru_cache(maxsize=4096)
def _read_raw_orientation_degrees(raw_path_str: str) -> int:
    raw_path = Path(raw_path_str)
    if not raw_path.exists():
        return 0
    try:
        proc = subprocess.run(
            ["exiftool", "-Orientation", "-n", str(raw_path)],
            capture_output=True,
            text=True,
            timeout=2.5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout and ":" in proc.stdout:
            raw_val = proc.stdout.split(":", 1)[1].strip().splitlines()[0].strip()
            try:
                n = int(raw_val)
            except ValueError:
                n = 1
            if n == 6:
                return 90
            if n == 8:
                return -90
            if n == 3:
                return 180
            return 0
    except Exception:
        pass
    try:
        import exifread  # type: ignore

        with open(raw_path, "rb") as f:
            tags = exifread.process_file(f, details=False, stop_tag="Image Orientation")
        v = tags.get("Image Orientation")
        if v is None:
            return 0
        return _coerce_raw_orientation_to_degrees(str(v))
    except Exception:
        return 0


def inject_orientation(entry: dict, resolver: PathResolver) -> dict:
    """Align ``rotate_degrees`` with how ``/image`` applies rotation.

    If the preview JPEG already has a non-default EXIF Orientation, only ``exif_transpose`` is used
    server-side — RAW orientation must not be applied again (avoids sideways thumbnails).

    When EXIF Orientation is 1 (missing/default) but RAW still encodes rotation (strip-export
    without EXIF), re-use ``rotate_degrees`` from RAW and swap layout width/height for 90°/270°.
    """
    p = entry.get("path", "")
    if not p or not os.path.isfile(p):
        return entry
    file_name = entry.get("file") or (os.path.basename(p) if p else "")
    if not file_name:
        return entry

    try:
        jpeg_orient = read_exif_orientation_tag(p)
        if jpeg_orient != 1:
            entry.pop("rotate_degrees", None)
            sync_gallery_entry_display_dimensions(entry)
            return entry

        raw_path = resolver.resolve_raw(file_name)
        if not raw_path or not raw_path.is_file():
            entry.pop("rotate_degrees", None)
            sync_gallery_entry_display_dimensions(entry)
            return entry

        raw_deg = _read_raw_orientation_degrees(str(raw_path))
        if not raw_deg:
            entry.pop("rotate_degrees", None)
            sync_gallery_entry_display_dimensions(entry)
            return entry

        entry["rotate_degrees"] = raw_deg
        sync_gallery_entry_display_dimensions(entry)
        return entry
    except Exception:
        logger.warning("inject_orientation failed for %s", p, exc_info=True)
        return entry


def read_orientation_degrees_from_raw(raw_path: Path | None) -> int:
    if raw_path is None or not raw_path.is_file():
        return 0
    return _read_raw_orientation_degrees(str(raw_path))


def load_results(base_dir: str) -> list[dict]:
    resolver = PathResolver(Path(base_dir))
    cleaned: list[dict] = []
    raw = load_raw_results(base_dir)
    if not raw:
        raw = _discover_gallery_rows_from_disk(base_dir)
    for entry in raw:
        try:
            normalize_scores(entry)
            resolve_paths(entry, base_dir)
            entry.setdefault("algorithm_version", "V3.2 Masterpiece")
            inject_layout(entry)
            inject_orientation(entry, resolver)
            cleaned.append(entry)
        except Exception:
            logger.warning("load_results: skip bad row %s", entry.get("file") or entry.get("path"), exc_info=True)
    return cleaned


def load_gallery_page(
    base_dir: str,
    sort: str,
    offset: int,
    limit: int,
    *,
    lite: bool = True,
    dedupe: bool = True,
) -> tuple[list[dict], int, int, int, bool, int]:
    """Paginated gallery slice using a frozen JSON cache + indexed sort.

    When ``lite`` is True (default), rows only get path/score normalization — no per-file
    JPEG layout or RAW orientation probes (fast Lab grid). Pass ``lite=False`` for full
    enrichment (same as ``load_results`` per row).
    """
    json_abs = os.path.abspath(_results_json_path(base_dir))
    json_rows = _read_json_rows_cached(json_abs)
    disk_rows = _discover_gallery_rows_from_disk(base_dir)
    # Always union: scored JSON rows win; unscored on-disk previews fill gaps mid-job.
    rows_ro = merge_json_and_disk_gallery_rows(json_rows, disk_rows)
    total_raw = len(rows_ro)

    from utils.config_loader import ConfigLoader

    from services.taste_profile import personalized_sort_metric, read_taste_profile

    taste_profile = read_taste_profile(base_dir) if sort == "personalized" else None

    def _row_sort_key(row: dict) -> float:
        try:
            if sort == "personalized":
                return personalized_sort_metric(row, taste_profile)
            return _sort_metric(row, sort)
        except Exception:
            return 0.0

    members_by_rep: dict[int, list[int]] = {}
    group_id_by_rep: dict[int, int] = {}
    max_members = 0
    if sort == "diverse":
        from services.diversity_selector import apply_diversity_selection, diversity_settings

        div_settings = diversity_settings(ConfigLoader.load())
        max_members = int(div_settings.get("max_members_returned", 40))
        if div_settings.get("enabled", True):
            indices, members_by_rep, group_id_by_rep = apply_diversity_selection(
                rows_ro,
                div_settings,
                order_key_fn=lambda row: _sort_metric(row, "overall"),
            )
            total = len(indices)
        else:
            indices = sorted(range(total_raw), key=lambda i: _sort_metric(rows_ro[i], "overall"), reverse=True)
            total = total_raw
    else:
        from services.gallery_dedupe import apply_gallery_view_dedupe, gallery_view_dedupe_settings

        dedupe_settings = gallery_view_dedupe_settings(ConfigLoader.load())
        if dedupe and dedupe_settings.get("enabled", True):
            indices, total, _raw = apply_gallery_view_dedupe(
                rows_ro,
                sort,
                settings=dedupe_settings,
                sort_key_fn=_row_sort_key,
            )
        else:
            indices = sorted(range(total_raw), key=lambda i: _row_sort_key(rows_ro[i]), reverse=True)
            total = total_raw

    start = min(offset, total)
    end = min(start + limit, total)
    has_more = end < total
    selected = indices[start:end]

    resolver = PathResolver(Path(base_dir)) if not lite else None
    cleaned: list[dict] = []
    for idx in selected:
        try:
            entry = dict(rows_ro[idx])
            normalize_scores(entry)
            resolve_paths(entry, base_dir)
            entry.setdefault("algorithm_version", "V3.2 Masterpiece")
            if not lite and resolver is not None:
                inject_layout(entry)
                inject_orientation(entry, resolver)
            if sort == "diverse":
                member_idxs = members_by_rep.get(idx, [])
                entry["group_id"] = group_id_by_rep.get(idx, 0)
                entry["is_representative"] = True
                entry["group_size"] = len(member_idxs) + 1
                entry["group_members"] = [
                    _compact_member(rows_ro[m], base_dir) for m in member_idxs[:max_members]
                ]
            cleaned.append(entry)
        except Exception:
            logger.warning("load_gallery_page: skip idx=%s row=%s", idx, rows_ro[idx].get("file"), exc_info=True)

    return cleaned, total, start, end, has_more, total_raw


def _compact_member(row: dict, base_dir: str) -> dict:
    """Minimal payload for a folded (non-representative) frame in a diverse group."""
    entry = dict(row)
    normalize_scores(entry)
    resolve_paths(entry, base_dir)
    return {
        "file": entry.get("file"),
        "path": entry.get("path"),
        "path_quoted": entry.get("path_quoted", ""),
        "before_path_quoted": entry.get("before_path_quoted", ""),
        "overall_score": entry.get("overall_score", 0.0),
        "category": entry.get("category"),
    }
