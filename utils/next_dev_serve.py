"""Start Next.js (`pnpm dev` / `npm run dev`) from ``web/`` in a background subprocess.

Mirrors :mod:`utils.gallery_serve` lifecycle: optional skip if port busy, log file under ``web/``,
Unix ``start_new_session`` / Windows detached flags, parent closes log fd after spawn.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from utils.gallery_serve import is_port_open
from utils.infra_listen_probe import next_js_listen_probe
from utils.repo_paths import repo_root

DEFAULT_NEXT_PORT = 3000

_POLL_INTERVAL_S = 0.4
# Next.js first compile can take tens of seconds on cold start.
_POLL_TIMEOUT_S = 120.0


def _empty_result(web_root: Path, port: int, log_path: Path) -> Dict[str, Any]:
    return {
        "started": False,
        "skipped": False,
        "ready": False,
        "url": f"http://127.0.0.1:{port}",
        "web_root": str(web_root),
        "port": port,
        "pid": None,
        "log_file": str(log_path),
        "reason": "",
        "command": "",
    }


def _finish(out: Dict[str, Any]) -> Dict[str, Any]:
    if out.get("skipped"):
        out["status"] = "skipped"
    elif out.get("started") and out.get("ready"):
        out["status"] = "ready"
    elif out.get("started"):
        out["status"] = "started_not_ready"
    else:
        out["status"] = "failed"
    return out


def _resolve_dev_command(web_root: Path) -> tuple[list[str], str]:
    """Prefer pnpm when lockfile present and pnpm is on PATH; else npm."""
    pkg = web_root / "package.json"
    if not pkg.is_file():
        return [], "no package.json"
    lock_pnpm = web_root / "pnpm-lock.yaml"
    npm_exe = shutil.which("npm")
    pnpm_exe = shutil.which("pnpm")
    if lock_pnpm.is_file() and pnpm_exe:
        return ["pnpm", "dev"], "pnpm dev"
    if pnpm_exe and not (web_root / "package-lock.json").is_file():
        return ["pnpm", "dev"], "pnpm dev"
    if npm_exe:
        return ["npm", "run", "dev"], "npm run dev"
    return [], "neither pnpm nor npm found on PATH"


def _wait_port(host: str, port: int) -> bool:
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        if is_port_open(host, port, timeout_s=0.5):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def start_next_dev_background(
    *,
    web_root: str | Path | None = None,
    port: int = DEFAULT_NEXT_PORT,
    gallery_api_origin: str | None = None,
) -> Dict[str, Any]:
    """Launch Next.js dev server from repo ``web/``; stdout/stderr -> ``web/next_dev.log``.

    When ``gallery_api_origin`` is set (e.g. ``http://127.0.0.1:8080``), it is passed as
    ``GALLERY_API_ORIGIN`` so ``next.config.js`` rewrites match the FastAPI port started by
    ``run_pipeline.py`` (no manual ``web/.env.local`` edit required).
    """
    root = Path(web_root).resolve() if web_root is not None else repo_root() / "web"
    log_path = root / "next_dev.log"
    out: Dict[str, Any] = _empty_result(root, port, log_path)

    if not root.is_dir():
        out["reason"] = "web root is not a directory"
        return _finish(out)

    if not (root / "package.json").is_file():
        out["reason"] = "web/package.json missing"
        return _finish(out)

    if not (root / "node_modules").is_dir():
        out["reason"] = "web/node_modules missing; run pnpm install or npm install in web/"
        return _finish(out)

    if is_port_open("127.0.0.1", port):
        probe_ok = next_js_listen_probe(port)
        out["skipped"] = True
        out["reason"] = (
            f"port {port} already in use (next_probe_ok={probe_ok})"
            if probe_ok
            else f"port {port} already in use (listener does not look like Next.js)"
        )
        out["ready"] = probe_ok
        out["listener_probe_ok"] = probe_ok
        return _finish(out)

    cmd, cmd_label = _resolve_dev_command(root)
    out["command"] = cmd_label
    if not cmd:
        out["reason"] = cmd_label
        return _finish(out)

    popen_kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True
        popen_kwargs["close_fds"] = True

    env = os.environ.copy()
    env["PORT"] = str(port)
    if gallery_api_origin and str(gallery_api_origin).strip():
        env["GALLERY_API_ORIGIN"] = str(gallery_api_origin).strip().rstrip("/")

    logf = None
    proc: Optional[subprocess.Popen] = None
    try:
        logf = open(log_path, "a", encoding="utf-8")
        logf.write("\n--- next dev (auto from run_pipeline.py) ---\n")
        logf.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=str(root),
            env=env,
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
    except OSError as e:
        out["reason"] = str(e)
        return _finish(out)
    finally:
        if logf is not None:
            try:
                logf.close()
            except OSError:
                pass

    assert proc is not None
    out["pid"] = proc.pid
    out["started"] = True

    ready = _wait_port("127.0.0.1", port)
    out["ready"] = ready
    if not ready:
        out["reason"] = "process started but port not listening within timeout; see web/next_dev.log"

    return _finish(out)
