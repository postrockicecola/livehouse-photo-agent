"""Start the FastAPI gallery_server (双排流预览) in a background subprocess.

跨平台策略摘要
----------------
* **Unix (macOS / Linux)**：``start_new_session=True`` 让子进程脱离当前会话，父进程退出后
  子进程仍可继续服务；``close_fds=True`` 避免子进程继承无关句柄（stdin/stdout/stderr
  及传给 Popen 的句柄会按 CPython 规则保留）。
* **Windows**：``CREATE_NEW_PROCESS_GROUP`` 便于 Ctrl+C 不连带杀子进程；
  ``DETACHED_PROCESS`` 弱化与控制台关联，更接近「后台服务」行为。

日志句柄策略
------------
* 父进程 ``open`` 日志文件 → ``Popen(..., stdout=logf, stderr=STDOUT)`` 启动子进程后，
  **立即在父进程 ``close()`` 日志文件对象**。
* 子进程在 fork/exec（或 Windows 的句柄复制）之后已持有自己的 stdout 副本，指向同一
  日志文件；父进程关闭自己的 fd **不会**关掉子进程仍在写入的底层文件（引用计数 /
  独立句柄语义由 OS 保证）。
* 若 ``Popen`` 在启动前失败，父进程负责 ``close`` 并删除空文件（可选，此处仅 close），
  避免句柄泄露。

端口就绪检测
------------
* 不再使用单次 ``sleep(0.6)``，改为 **0.2s 间隔轮询，最长约 3s**，降低「偶发慢启动」
  误判与无意义长等。
"""
from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from utils.repo_paths import repo_root

DEFAULT_GALLERY_PORT = 8080

# Port poll: balance responsiveness vs CPU wakeups.
_POLL_INTERVAL_S = 0.2
_POLL_TIMEOUT_S = 3.0


def _empty_result(source_dir: Path, port: int, log_path: Path) -> Dict[str, Any]:
    """Unified return shape: every key always present (callers avoid KeyError)."""
    return {
        "started": False,
        "skipped": False,
        "ready": False,
        "url": f"http://127.0.0.1:{port}",
        "source_dir": str(source_dir),
        "port": port,
        "pid": None,
        "log_file": str(log_path),
        "reason": "",
    }


def is_port_open(host: str, port: int, *, timeout_s: float = 0.4) -> bool:
    """Return True if something accepts TCP connections on host:port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _wait_port_listening(host: str, port: int) -> bool:
    """Poll until port accepts or timeout (_POLL_TIMEOUT_S)."""
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while time.monotonic() < deadline:
        if is_port_open(host, port, timeout_s=0.35):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def start_gallery_server_background(
    source_dir: str | Path,
    port: int = DEFAULT_GALLERY_PORT,
) -> Dict[str, Any]:
    """
    Launch gallery_server.py with BASE_DIR=source_dir in the background.

    Stdout/stderr go to ``<source_dir>/gallery_server.log``. Parent does not keep the log
    file object open after spawn (see module docstring).
    """
    source_dir = Path(source_dir).resolve()
    log_path = source_dir / "gallery_server.log"
    out: Dict[str, Any] = _empty_result(source_dir, port, log_path)

    if not source_dir.is_dir():
        out["reason"] = "source_dir is not a directory"
        return out

    server_script = repo_root() / "gallery_server.py"
    if not server_script.is_file():
        out["reason"] = f"gallery_server.py not found at {server_script}"
        return out

    if is_port_open("127.0.0.1", port):
        out["skipped"] = True
        out["reason"] = f"port {port} already in use"
        return out

    # --- Subprocess kwargs: platform-specific isolation + fd hygiene ---
    popen_kwargs: Dict[str, Any] = {}
    if sys.platform == "win32":
        # CREATE_NEW_PROCESS_GROUP: console Ctrl+C does not kill the whole group blindly.
        # DETACHED_PROCESS: reduce console attachment (background service–like).
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
        creationflags |= int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
        popen_kwargs["creationflags"] = creationflags
        # Windows: close_fds is documented as largely ignored when stdio redirected; omit False.
    else:
        popen_kwargs["start_new_session"] = True
        popen_kwargs["close_fds"] = True

    logf = None
    proc: Optional[subprocess.Popen] = None
    try:
        logf = open(log_path, "a", encoding="utf-8")
        logf.write("\n--- gallery_server (auto) ---\n")
        logf.flush()

        proc = subprocess.Popen(
            [sys.executable, str(server_script), str(source_dir), str(port)],
            cwd=str(source_dir),
            stdout=logf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            **popen_kwargs,
        )
    except OSError as e:
        out["reason"] = str(e)
        return out
    finally:
        # Parent must not hold the log fd: after a successful spawn the child holds its own
        # duplicate; on failure no child exists and we must release the handle here.
        if logf is not None:
            try:
                logf.close()
            except OSError:
                pass

    assert proc is not None
    out["pid"] = proc.pid
    out["started"] = True

    ready = _wait_port_listening("127.0.0.1", port)
    out["ready"] = ready
    if not ready:
        out["reason"] = "process started but port not listening within timeout; see log file"

    return out
