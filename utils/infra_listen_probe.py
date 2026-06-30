"""Lightweight local HTTP probes to tell Livehouse FastAPI / Next dev from other listeners."""
from __future__ import annotations

from urllib.request import urlopen


def livehouse_openapi_probe(port: int, *, host: str = "127.0.0.1", timeout_s: float = 1.0) -> bool:
    """True if ``/openapi.json`` looks like this repo's ``gallery_server`` (title in OpenAPI spec)."""
    try:
        with urlopen(f"http://{host}:{port}/openapi.json", timeout=timeout_s) as resp:
            if int(resp.status) != 200:
                return False
            chunk = resp.read(16000)
        return b"Livehouse Gallery API" in chunk
    except Exception:
        return False


def next_js_listen_probe(port: int, *, host: str = "127.0.0.1", timeout_s: float = 1.0) -> bool:
    """Best-effort: Next.js dev often sets ``X-Powered-By: Next.js`` on ``/``."""
    try:
        with urlopen(f"http://{host}:{port}/", timeout=timeout_s) as resp:
            powered = (resp.headers.get("X-Powered-By") or "") + " " + (resp.headers.get("Server") or "")
        return "next" in powered.lower()
    except Exception:
        return False
