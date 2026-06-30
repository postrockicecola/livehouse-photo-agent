"""Persist homepage curation + export prefs under ``Previews/runtime/gallery_curation.json``."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path

CURATION_FILENAME = "gallery_curation.json"
CURATION_VERSION = 2
_MAX_FEEDBACK = 10_000
_MAX_EXPORT_ENTRIES = 2_000

FeedbackVerdict = Literal["liked", "pass", "rejected"]
VERDICTS: frozenset[str] = frozenset({"liked", "pass", "rejected"})

# Photographer-facing chips; loosely aligned with Stage3 dims (moment_peak, atmosphere_impact, …).
LIKE_REASONS: frozenset[str] = frozenset(
    {
        "moment",
        "atmosphere",
        "light",
        "composition",
        "subject",
        "editable",
        "expression",
        "energy",
        "color",
        "clarity",
        "exposure_fit",
        "narrative",
    }
)
REJECT_REASONS: frozenset[str] = frozenset(
    {
        "blur_bad",
        "subject_bad",
        "light_bad",
        "composition_bad",
        "duplicate",
        "emotion_weak",
        "timing_bad",
        "obstructed",
        "background_bad",
        "exposure_bad",
        "distracting",
    }
)

LIKE_REASON_LABELS: dict[str, str] = {
    "moment": "瞬间",
    "atmosphere": "氛围",
    "light": "光影",
    "composition": "构图",
    "subject": "主体",
    "editable": "可后期",
    "expression": "表情神态",
    "energy": "现场张力",
    "color": "色彩",
    "clarity": "清晰",
    "exposure_fit": "曝光舒服",
    "narrative": "叙事感",
}

REJECT_REASON_LABELS: dict[str, str] = {
    "blur_bad": "模糊",
    "subject_bad": "主体差",
    "light_bad": "光影差",
    "composition_bad": "构图差",
    "duplicate": "重复",
    "emotion_weak": "情绪弱",
    "timing_bad": "时机不对",
    "obstructed": "遮挡",
    "background_bad": "背景乱",
    "exposure_bad": "曝光翻车",
    "distracting": "干扰元素",
}


def gallery_curation_path(previews_dir: str | Path) -> Path:
    return runtime_file_path(previews_dir, CURATION_FILENAME)


def _sanitize_reason_list(raw: Any, allowed: frozenset[str]) -> list[str]:
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        s = str(item).strip()
        if not s or s not in allowed or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _sanitize_feedback_entry(key: str, raw: Any) -> dict[str, Any] | None:
    pk = str(key).strip()
    if not pk or not isinstance(raw, dict):
        return None
    verdict = str(raw.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        return None
    entry: dict[str, Any] = {"verdict": verdict}
    like_r = _sanitize_reason_list(raw.get("like_reasons"), LIKE_REASONS)
    reject_r = _sanitize_reason_list(raw.get("reject_reasons"), REJECT_REASONS)
    if verdict == "liked" and like_r:
        entry["like_reasons"] = like_r
    if verdict == "rejected" and reject_r:
        entry["reject_reasons"] = reject_r
    if verdict == "pass" and reject_r:
        entry["reject_reasons"] = reject_r
    note = raw.get("note")
    if isinstance(note, str) and note.strip():
        entry["note"] = note.strip()[:500]
    return entry


def normalize_gallery_curation(raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Canonical v2 document (in-memory). Migrates legacy v1 ``selected_keys`` only.
    """
    feedback: dict[str, dict[str, Any]] = {}
    export_by_file: dict[str, Any] = {}

    if raw:
        if isinstance(raw.get("export_by_file"), dict):
            export_by_file = dict(raw["export_by_file"])
        fb = raw.get("feedback_by_key")
        if isinstance(fb, dict):
            for k, v in fb.items():
                entry = _sanitize_feedback_entry(k, v)
                if entry:
                    feedback[str(k).strip()] = entry
        for k in raw.get("selected_keys") or []:
            sk = str(k).strip()
            if sk and sk not in feedback:
                feedback[sk] = {"verdict": "liked"}

    selected_keys = [k for k, e in feedback.items() if e.get("verdict") == "liked"]

    return {
        "version": CURATION_VERSION,
        "selected_keys": selected_keys,
        "feedback_by_key": feedback,
        "export_by_file": export_by_file,
        "updated_unix": int(raw.get("updated_unix") or time.time()) if raw else int(time.time()),
    }


def read_gallery_curation(previews_dir: str | Path) -> dict[str, Any] | None:
    path = resolve_runtime_file(previews_dir, CURATION_FILENAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return normalize_gallery_curation(data)


def curation_liked_keys(curation: dict[str, Any] | None) -> set[str]:
    if not curation:
        return set()
    norm = normalize_gallery_curation(curation) if curation.get("version") != CURATION_VERSION else curation
    fb = norm.get("feedback_by_key") or {}
    return {k for k, e in fb.items() if isinstance(e, dict) and e.get("verdict") == "liked"}


def curation_keys_by_verdict(curation: dict[str, Any] | None, verdict: FeedbackVerdict) -> set[str]:
    if not curation:
        return set()
    norm = normalize_gallery_curation(curation) if curation.get("version") != CURATION_VERSION else curation
    fb = norm.get("feedback_by_key") or {}
    return {k for k, e in fb.items() if isinstance(e, dict) and e.get("verdict") == verdict}


def _sanitize_export_entry(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    file_name = str(raw.get("file") or "").strip()
    if not file_name:
        return None
    out: dict[str, Any] = {"file": file_name}
    rot = raw.get("rotate")
    if rot is not None:
        try:
            out["rotate"] = int(rot)
        except (TypeError, ValueError):
            out["rotate"] = 0
    for key in ("film_variant", "film_source_path_quoted", "alternate_jpeg_path_quoted"):
        v = raw.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()
    return out


def write_gallery_curation(
    previews_dir: str | Path,
    *,
    selected_keys: list[str] | None = None,
    feedback_by_key: dict[str, Any] | None = None,
    export_by_file: dict[str, Any] | None = None,
) -> Path | None:
    """
    Write v2 curation. Provide ``feedback_by_key`` and/or legacy ``selected_keys``
    (merged as ``liked`` without clobbering explicit pass/rejected entries).
    """
    merged_feedback: dict[str, dict[str, Any]] = {}

    if feedback_by_key:
        for k, v in list(feedback_by_key.items())[:_MAX_FEEDBACK]:
            entry = _sanitize_feedback_entry(k, v)
            if entry:
                merged_feedback[str(k).strip()] = entry

    if selected_keys:
        for k in selected_keys:
            sk = str(k).strip()
            if not sk:
                continue
            if sk not in merged_feedback:
                merged_feedback[sk] = {"verdict": "liked"}
            elif merged_feedback[sk].get("verdict") != "liked":
                merged_feedback[sk] = {"verdict": "liked"}

    norm = normalize_gallery_curation(
        {
            "feedback_by_key": merged_feedback,
            "export_by_file": export_by_file or {},
        }
    )

    export_out: dict[str, Any] = {}
    src_export = export_by_file if export_by_file is not None else norm.get("export_by_file") or {}
    for pref_key, entry in list(src_export.items())[:_MAX_EXPORT_ENTRIES]:
        pk = str(pref_key).strip()
        if not pk:
            continue
        clean = _sanitize_export_entry(entry)
        if clean:
            export_out[pk] = clean

    payload = {
        "version": CURATION_VERSION,
        "selected_keys": norm["selected_keys"],
        "feedback_by_key": norm["feedback_by_key"],
        "export_by_file": export_out,
        "updated_unix": int(time.time()),
    }

    path = gallery_curation_path(previews_dir)
    try:
        runtime_dir(previews_dir, create=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def clear_gallery_curation(previews_dir: str | Path) -> bool:
    path = gallery_curation_path(previews_dir)
    try:
        if path.is_file():
            path.unlink()
        return True
    except OSError:
        return False
