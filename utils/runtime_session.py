"""Persist which Previews directory is «active» for gallery API consumers.

``api.gallery_routes._runtime_base_dir`` reads ``<archive_root>/runtime/latest_session.json``
so a long-lived ``gallery_server`` process can switch sessions without restarting.

Layout assumption (same as :class:`services.path_service.PathResolver`): ``previews_dir`` is
``…/<archive>/<session>/Previews`` so ``archive_root = previews_dir.parent.parent``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from utils.runtime_paths import resolve_runtime_file, runtime_dir, runtime_file_path


def iter_candidate_archive_roots(*, base_dir: str | Path) -> list[Path]:
    """Archive roots that may contain ``runtime/latest_session.json`` (multi-archive / Studio activate)."""
    import os

    roots: list[Path] = []
    seen: set[str] = set()

    def add(raw: str | Path | None) -> None:
        if raw is None:
            return
        s = str(raw).strip()
        if not s:
            return
        try:
            r = Path(s).expanduser().resolve()
        except OSError:
            return
        if not r.is_dir():
            return
        key = str(r)
        if key in seen:
            return
        seen.add(key)
        roots.append(r)

    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        add(env)

    override = (os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR") or "").strip()
    if override:
        ar = archive_root_for_previews(override)
        if ar is not None:
            add(ar)

    try:
        from utils.studio_ingest_config import read_ingest_config

        ar_s = str(read_ingest_config().get("archive_root") or "").strip()
        if ar_s:
            add(ar_s)
    except Exception:
        pass

    base_path = Path(base_dir).expanduser().resolve()
    ar = archive_root_for_previews(base_path)
    if ar is not None:
        add(ar)

    try:
        from utils.luma_brain import brain_connect

        conn = brain_connect()
        try:
            rows = conn.execute(
                """
                SELECT DISTINCT archive_root FROM sessions
                WHERE archive_root IS NOT NULL AND TRIM(archive_root) != ''
                """
            ).fetchall()
            for row in rows:
                add(row[0])
        finally:
            conn.close()
    except Exception:
        pass

    from utils.studio_sessions import read_source_dir_from_yaml, resolve_default_archive_root

    source_hint = str(base_path)
    cfg_sd = read_source_dir_from_yaml()
    if cfg_sd:
        source_hint = str(cfg_sd)
    try:
        from utils.config_loader import ConfigLoader

        yaml_sd = (ConfigLoader.load().get("paths") or {}).get("source_dir")
        if yaml_sd:
            source_hint = str(yaml_sd)
    except Exception:
        pass
    add(resolve_default_archive_root(source_hint))
    return roots


def read_newest_latest_session_pointer(
    *,
    base_dir: str | Path,
) -> tuple[Path, dict[str, Any]] | None:
    """Newest ``latest_session.json`` among candidate archives whose ``previews_dir`` exists."""
    best: tuple[float, Path, dict[str, Any]] | None = None
    for ar in iter_candidate_archive_roots(base_dir=base_dir):
        refp = resolve_runtime_file(ar, "latest_session.json")
        if refp is None:
            continue
        ref = read_latest_session_pointer(ar)
        if not ref:
            continue
        previews_s = str(ref.get("previews_dir") or "").strip()
        if not previews_s:
            continue
        try:
            cand = Path(previews_s).expanduser().resolve()
        except OSError:
            continue
        if not cand.is_dir():
            continue
        try:
            mt = float(refp.stat().st_mtime)
        except OSError:
            mt = 0.0
        if best is None or mt > best[0]:
            best = (mt, refp, ref)
    if best is None:
        return None
    return best[1], best[2]


def resolve_archive_root_for_runtime(*, base_dir: str | Path) -> Path:
    """Locate ``<archive>/runtime/latest_session.json`` for gallery API hot path."""
    import os

    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()

    override = (os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR") or "").strip()
    if override:
        ar = archive_root_for_previews(override)
        if ar is not None and ar.is_dir():
            return ar

    base_path = Path(base_dir).expanduser().resolve()
    ar = archive_root_for_previews(base_path)
    if ar is not None and ar.is_dir():
        return ar

    try:
        from utils.studio_ingest_config import read_ingest_config_raw

        ar_s = str((read_ingest_config_raw() or {}).get("archive_root") or "").strip()
        if ar_s:
            p = Path(ar_s).expanduser()
            if p.is_dir():
                return p.resolve()
    except Exception:
        pass

    if base_path.name.lower() == "previews" and base_path.parent.parent.is_dir():
        legacy = base_path.parent.parent
        if resolve_runtime_file(legacy, "latest_session.json") is not None:
            return legacy.resolve()

    try:
        from utils.luma_brain import brain_connect

        conn = brain_connect()
        try:
            row = conn.execute(
                """
                SELECT archive_root FROM sessions
                WHERE archive_root IS NOT NULL AND TRIM(archive_root) != ''
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            if row and row[0]:
                p = Path(str(row[0])).expanduser()
                if p.is_dir():
                    return p.resolve()
        finally:
            conn.close()
    except Exception:
        pass

    from utils.studio_sessions import read_source_dir_from_yaml, resolve_default_archive_root

    source_hint = str(base_path)
    cfg_sd = read_source_dir_from_yaml()
    if cfg_sd:
        source_hint = str(cfg_sd)
    try:
        from utils.config_loader import ConfigLoader

        yaml_sd = (ConfigLoader.load().get("paths") or {}).get("source_dir")
        if yaml_sd:
            source_hint = str(yaml_sd)
    except Exception:
        pass
    return resolve_default_archive_root(source_hint)


def archive_root_for_previews(previews_dir: str | Path) -> Path | None:
    previews = Path(previews_dir).expanduser()
    try:
        previews = previews.resolve()
    except OSError:
        return None
    if previews.name.lower() != "previews" or not previews.parent:
        return None
    return previews.parent.parent


def read_latest_session_pointer(archive_root: str | Path) -> dict[str, Any] | None:
    """Read ``latest_session.json`` for *archive_root*; return parsed dict or None."""
    from utils.runtime_paths import resolve_runtime_file

    refp = resolve_runtime_file(archive_root, "latest_session.json")
    if refp is None or not refp.is_file():
        return None
    try:
        with refp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def write_latest_session_pointer(previews_dir: str | Path) -> Path | None:
    """Write ``latest_session.json`` under the archive root. Returns path written, or None."""
    previews = Path(previews_dir).expanduser().resolve()
    if not previews.is_dir():
        return None
    session_dir = previews.parent
    archive_root = session_dir.parent
    try:
        runtime_dir(archive_root, create=True)
    except OSError:
        return None

    payload: dict[str, str] = {
        "previews_dir": str(previews),
        "session_dir": str(session_dir.resolve()),
    }
    for name in ("RAW", "Raw", "raw"):
        cand = session_dir / name
        if cand.is_dir():
            payload["raw_dir"] = str(cand.resolve())
            break

    path = runtime_file_path(archive_root, "latest_session.json")
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return None
    return path
