"""Canonical session runtime directory: ``runtime/`` (legacy: ``.runtime/``)."""
from __future__ import annotations

from pathlib import Path

RUNTIME_DIR_NAME = "runtime"
LEGACY_RUNTIME_DIR_NAME = ".runtime"


def runtime_dir(parent: str | Path, *, create: bool = False) -> Path:
    """Directory used for new writes under *parent* (Previews dir or archive root)."""
    d = Path(parent).expanduser().resolve() / RUNTIME_DIR_NAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def legacy_runtime_dir(parent: str | Path) -> Path:
    return Path(parent).expanduser().resolve() / LEGACY_RUNTIME_DIR_NAME


def resolve_runtime_dir(parent: str | Path) -> Path:
    """Existing runtime folder for reads: prefer ``runtime/``, else legacy ``.runtime/``."""
    p = Path(parent).expanduser().resolve()
    new = p / RUNTIME_DIR_NAME
    leg = p / LEGACY_RUNTIME_DIR_NAME
    if new.is_dir():
        return new
    if leg.is_dir():
        return leg
    return new


def resolve_runtime_file(parent: str | Path, filename: str) -> Path | None:
    """Return path if *filename* exists under ``runtime/`` or legacy ``.runtime/``."""
    for sub in (RUNTIME_DIR_NAME, LEGACY_RUNTIME_DIR_NAME):
        f = Path(parent).expanduser().resolve() / sub / filename
        if f.is_file():
            return f
    return None


def runtime_file_path(parent: str | Path, filename: str) -> Path:
    """Target path for writes (under ``runtime/``; file may not exist yet)."""
    return runtime_dir(parent) / filename
