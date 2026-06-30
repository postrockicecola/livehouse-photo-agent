"""Persist session-level vibe (natural language → film variant) under Previews ``runtime/``."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path

SESSION_VIBE_FILENAME = "session_vibe.json"


def session_vibe_path(previews_dir: str | Path) -> Path:
    return runtime_file_path(previews_dir, SESSION_VIBE_FILENAME)


def read_session_vibe(previews_dir: str | Path) -> dict[str, Any] | None:
    path = resolve_runtime_file(previews_dir, SESSION_VIBE_FILENAME)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_session_vibe(previews_dir: str | Path, payload: dict[str, Any]) -> Path | None:
    path = session_vibe_path(previews_dir)
    try:
        runtime_dir(previews_dir, create=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path
    except OSError:
        return None


def clear_session_vibe(previews_dir: str | Path) -> bool:
    path = session_vibe_path(previews_dir)
    try:
        if path.is_file():
            path.unlink()
        return True
    except OSError:
        return False
