"""Strong-supervision export log under ``Previews/runtime/export_feedback.json`` (decoupled from curation)."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path

EXPORT_FEEDBACK_FILENAME = "export_feedback.json"
EXPORT_FEEDBACK_VERSION = 1
_MAX_EVENTS = 500
_MAX_ITEMS_PER_EVENT = 500


def export_feedback_path(previews_dir: str | Path) -> Path:
    return runtime_file_path(previews_dir, EXPORT_FEEDBACK_FILENAME)


def _sanitize_export_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    file_name = str(raw.get("file") or "").strip()
    if not file_name:
        return None
    out: dict[str, Any] = {"file": file_name}
    try:
        out["rotate"] = int(raw.get("rotate") or 0)
    except (TypeError, ValueError):
        out["rotate"] = 0
    for key in (
        "film_variant",
        "film_variant_effective",
        "film_source_path_quoted",
        "alternate_jpeg_path_quoted",
    ):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()
    for flag in ("jpeg_exported", "raw_copied", "graded_from_raw"):
        if raw.get(flag) is True:
            out[flag] = True
    return out


def _sanitize_event(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    try:
        exported_unix = int(raw.get("exported_unix") or 0)
    except (TypeError, ValueError):
        exported_unix = 0
    if exported_unix <= 0:
        return None
    items_in: list[Any] = raw.get("items") if isinstance(raw.get("items"), list) else []
    items: list[dict[str, Any]] = []
    for it in items_in[:_MAX_ITEMS_PER_EVENT]:
        clean = _sanitize_export_item(it)
        if clean:
            items.append(clean)
    if not items:
        return None
    eid = str(raw.get("id") or "").strip() or str(uuid.uuid4())
    out: dict[str, Any] = {
        "id": eid[:128],
        "exported_unix": exported_unix,
        "use_session_vibe": bool(raw.get("use_session_vibe")),
        "items": items,
    }
    cat = raw.get("category")
    if isinstance(cat, str) and cat.strip():
        out["category"] = cat.strip()[:64]
    ep = raw.get("export_path")
    if isinstance(ep, str) and ep.strip():
        out["export_path"] = ep.strip()[:1024]
    sv = raw.get("session_vibe_film_variant")
    if isinstance(sv, str) and sv.strip():
        out["session_vibe_film_variant"] = sv.strip()[:128]
    return out


def normalize_export_feedback(raw: dict[str, Any] | None) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    if raw and isinstance(raw.get("events"), list):
        for ev in raw["events"]:
            clean = _sanitize_event(ev)
            if clean:
                events.append(clean)
    events = events[-_MAX_EVENTS:]
    updated = int(raw.get("updated_unix") or time.time()) if raw else int(time.time())
    return {
        "version": EXPORT_FEEDBACK_VERSION,
        "events": events,
        "updated_unix": updated,
    }


def read_export_feedback(previews_dir: str | Path) -> dict[str, Any] | None:
    path = resolve_runtime_file(previews_dir, EXPORT_FEEDBACK_FILENAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize_export_feedback(data)


def write_export_feedback(previews_dir: str | Path, *, events: list[dict[str, Any]]) -> Path | None:
    norm = normalize_export_feedback({"events": events, "updated_unix": int(time.time())})
    path = export_feedback_path(previews_dir)
    try:
        runtime_dir(previews_dir, create=True)
        path.write_text(json.dumps(norm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def append_export_feedback_event(
    previews_dir: str | Path,
    *,
    category: str,
    use_session_vibe: bool,
    session_vibe_film_variant: str | None,
    export_path: str | None,
    items: list[dict[str, Any]],
    exported_unix: int | None = None,
) -> dict[str, Any] | None:
    """
    Append one batch export record. ``items`` rows use catalog ``file`` plus optional style fields.
    """
    clean_items: list[dict[str, Any]] = []
    for raw in items[:_MAX_ITEMS_PER_EVENT]:
        clean = _sanitize_export_item(raw)
        if clean:
            clean_items.append(clean)
    if not clean_items:
        return None

    cur = read_export_feedback(previews_dir) or normalize_export_feedback(None)
    events = list(cur.get("events") or [])
    ts = int(exported_unix or time.time())
    event: dict[str, Any] = {
        "id": f"export_{ts}_{uuid.uuid4().hex[:8]}",
        "exported_unix": ts,
        "use_session_vibe": bool(use_session_vibe),
        "items": clean_items,
    }
    if category.strip():
        event["category"] = category.strip()[:64]
    if export_path and export_path.strip():
        event["export_path"] = export_path.strip()[:1024]
    if session_vibe_film_variant and session_vibe_film_variant.strip():
        event["session_vibe_film_variant"] = session_vibe_film_variant.strip()[:128]

    events.append(event)
    written = write_export_feedback(previews_dir, events=events)
    return event if written else None


def exported_files_aggregate(previews_dir: str | Path) -> dict[str, int]:
    """Per catalog ``file`` basename: how many export events included it (for taste weighting)."""
    doc = read_export_feedback(previews_dir)
    if not doc:
        return {}
    counts: dict[str, int] = {}
    for ev in doc.get("events") or []:
        if not isinstance(ev, dict):
            continue
        for it in ev.get("items") or []:
            if not isinstance(it, dict):
                continue
            fn = str(it.get("file") or "").strip()
            if not fn:
                continue
            counts[fn] = counts.get(fn, 0) + 1
    return counts
