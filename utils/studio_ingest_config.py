"""Persist Studio ingest paths (monitor + archive + optional session folder name)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from utils.repo_paths import repo_root
from utils.studio_sessions import read_source_dir_from_yaml, resolve_default_archive_root

CONFIG_FILENAME = "studio_ingest.json"


def ingest_config_path() -> Path:
    return repo_root() / "configs" / CONFIG_FILENAME


def _default_archive_root() -> str:
    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return str(p.resolve())
    sd = read_source_dir_from_yaml() or "."
    try:
        from utils.config_loader import ConfigLoader

        cfg = ConfigLoader.load()
        yaml_sd = (cfg.get("paths") or {}).get("source_dir")
        if yaml_sd:
            sd = str(yaml_sd)
    except Exception:
        pass
    return str(resolve_default_archive_root(sd))


def ingest_config_defaults() -> dict[str, Any]:
    return {
        "ingest_monitor_path": "",
        "archive_root": _default_archive_root(),
        "session_folder_name": "",
        "session_folder_name_auto": True,
        "updated_at": None,
    }


def read_ingest_config_raw() -> dict[str, Any]:
    path = ingest_config_path()
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_ingest_config() -> dict[str, Any]:
    """Merged config for API responses (always includes effective paths)."""
    defaults = ingest_config_defaults()
    raw = read_ingest_config_raw()
    monitor = str(raw.get("ingest_monitor_path") or defaults["ingest_monitor_path"]).strip()
    archive = str(raw.get("archive_root") or defaults["archive_root"]).strip()
    if not archive:
        archive = defaults["archive_root"]
    session_name = str(raw.get("session_folder_name") or "").strip()
    auto_name = not session_name
    return {
        "ingest_monitor_path": monitor,
        "archive_root": archive,
        "session_folder_name": session_name,
        "session_folder_name_auto": auto_name,
        "updated_at": raw.get("updated_at"),
        "config_path": str(ingest_config_path()),
    }


def save_ingest_config(
    *,
    ingest_monitor_path: str | None = None,
    archive_root: str | None = None,
    session_folder_name: str | None = None,
) -> dict[str, Any]:
    """
    Persist ingest settings under ``configs/studio_ingest.json``.

    Empty *ingest_monitor_path* / *archive_root* keeps the previous stored value.
    Empty *session_folder_name* enables auto naming from photo EXIF date (ingest worker).
    """
    current = read_ingest_config()
    monitor = current["ingest_monitor_path"]
    archive = current["archive_root"]
    session_name = current["session_folder_name"]

    if ingest_monitor_path is not None:
        v = str(ingest_monitor_path).strip()
        if v:
            monitor = v
    if archive_root is not None:
        v = str(archive_root).strip()
        if v:
            ar = Path(v).expanduser()
            if not ar.is_dir():
                raise ValueError(f"archive_root is not a directory: {v}")
            archive = str(ar.resolve())
    if session_folder_name is not None:
        session_name = str(session_folder_name).strip()

    payload = {
        "ingest_monitor_path": monitor,
        "archive_root": archive,
        "session_folder_name": session_name,
        "updated_at": int(time.time()),
    }
    path = ingest_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return read_ingest_config()
