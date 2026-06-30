"""Pairwise photographer preferences (burst / similar frames) under ``Previews/runtime/pairwise_preferences.json``."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from utils.gallery_curation import LIKE_REASONS, REJECT_REASONS
from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path

PAIRWISE_FILENAME = "pairwise_preferences.json"
PAIRWISE_VERSION = 1
_MAX_ENTRIES = 5_000

PairwiseSource = Literal["burst", "manual", "lab_compare", "unknown"]

# Why the winner beat the loser — reuse curation vocab for taste/ranking alignment.
PAIRWISE_REASON_TAGS: frozenset[str] = frozenset(LIKE_REASONS | REJECT_REASONS | {"duplicate"})


def pairwise_preferences_path(previews_dir: str | Path) -> Path:
    return runtime_file_path(previews_dir, PAIRWISE_FILENAME)


def _sanitize_key(raw: Any) -> str:
    return str(raw or "").strip().replace("\\", "/")


def _sanitize_reason_tags(raw: Any) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip()
        if not s or s not in PAIRWISE_REASON_TAGS or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _sanitize_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    winner = _sanitize_key(raw.get("winner_key"))
    loser = _sanitize_key(raw.get("loser_key"))
    if not winner or not loser or winner == loser:
        return None
    group_raw = raw.get("group_id")
    group_id: str | None
    if group_raw is None or (isinstance(group_raw, str) and not group_raw.strip()):
        group_id = None
    else:
        group_id = _sanitize_key(group_raw) or None
    source = str(raw.get("source") or "unknown").strip().lower()
    if source not in ("burst", "manual", "lab_compare", "unknown"):
        source = "unknown"
    try:
        created = int(raw.get("created_unix") or time.time())
    except (TypeError, ValueError):
        created = int(time.time())
    entry: dict[str, Any] = {
        "winner_key": winner,
        "loser_key": loser,
        "group_id": group_id,
        "reason_tags": _sanitize_reason_tags(raw.get("reason_tags")),
        "created_unix": created,
        "source": source,
    }
    eid = raw.get("id")
    if isinstance(eid, str) and eid.strip():
        entry["id"] = eid.strip()[:128]
    return entry


def normalize_pairwise_preferences(raw: dict[str, Any] | None) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    if raw and isinstance(raw.get("entries"), list):
        for item in raw["entries"]:
            clean = _sanitize_entry(item)
            if clean:
                entries.append(clean)
    entries = entries[-_MAX_ENTRIES:]
    updated = int(raw.get("updated_unix") or time.time()) if raw else int(time.time())
    return {
        "version": PAIRWISE_VERSION,
        "entries": entries,
        "updated_unix": updated,
    }


def read_pairwise_preferences(previews_dir: str | Path) -> dict[str, Any] | None:
    path = resolve_runtime_file(previews_dir, PAIRWISE_FILENAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize_pairwise_preferences(data)


def write_pairwise_preferences(
    previews_dir: str | Path,
    *,
    entries: list[dict[str, Any]],
) -> Path | None:
    norm = normalize_pairwise_preferences({"entries": entries, "updated_unix": int(time.time())})
    path = pairwise_preferences_path(previews_dir)
    try:
        runtime_dir(previews_dir, create=True)
        path.write_text(json.dumps(norm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def append_pairwise_preferences(
    previews_dir: str | Path,
    new_entries: list[dict[str, Any]],
    *,
    replace_same_pair_in_group: bool = False,
) -> dict[str, Any]:
    """
    Append sanitized entries. When ``replace_same_pair_in_group`` is true, drop the latest
    prior entry with the same ``(winner_key, loser_key, group_id)`` before appending.
    """
    cur = read_pairwise_preferences(previews_dir) or normalize_pairwise_preferences(None)
    existing = list(cur.get("entries") or [])
    incoming: list[dict[str, Any]] = []
    for raw in new_entries:
        clean = _sanitize_entry(raw)
        if clean:
            incoming.append(clean)
    if not incoming:
        return {"ok": False, "error": "no_valid_entries", "count": len(existing)}

    if replace_same_pair_in_group:
        for inc in incoming:
            w, l, g = inc["winner_key"], inc["loser_key"], inc.get("group_id")
            existing = [
                e
                for e in existing
                if not (
                    e.get("winner_key") == w
                    and e.get("loser_key") == l
                    and e.get("group_id") == g
                )
            ]
            existing.append(inc)
    else:
        existing.extend(incoming)

    existing = existing[-_MAX_ENTRIES:]
    written = write_pairwise_preferences(previews_dir, entries=existing)
    if not written:
        return {"ok": False, "error": "write_failed", "count": len(existing)}
    return {
        "ok": True,
        "count": len(existing),
        "appended": len(incoming),
        "path": str(written),
    }


def clear_pairwise_preferences(previews_dir: str | Path) -> bool:
    path = pairwise_preferences_path(previews_dir)
    try:
        if path.is_file():
            path.unlink()
        return True
    except OSError:
        return False


def list_pairwise_entries(
    previews_dir: str | Path,
    *,
    group_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    doc = read_pairwise_preferences(previews_dir)
    if not doc:
        return []
    entries = list(doc.get("entries") or [])
    if group_id is not None:
        gid = _sanitize_key(group_id) or None
        entries = [e for e in entries if e.get("group_id") == gid]
    if limit is not None and limit > 0:
        entries = entries[-limit:]
    return entries
