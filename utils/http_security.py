"""Shared HTTP security helpers: path allowlists, ops auth, CORS, SSRF, rate limits."""
from __future__ import annotations

import ipaddress
import logging
import os
import socket
import threading
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence
from urllib.parse import urlparse

from fastapi import Header, HTTPException, Request

logger = logging.getLogger(__name__)


def path_is_under_roots(abs_path: Path, roots: Sequence[Path]) -> bool:
    """True when ``abs_path`` resolves under one of ``roots`` (symlink-aware).

    Works for existing files/dirs and for not-yet-created paths (uses resolved string).
    """
    try:
        p = abs_path.expanduser().resolve(strict=False)
    except OSError:
        return False
    try:
        p_real = os.path.realpath(p) if p.exists() else os.path.normpath(str(p))
    except OSError:
        p_real = os.path.normpath(str(p))

    for root in roots:
        try:
            r = root.expanduser().resolve(strict=False)
        except OSError:
            continue
        try:
            p.relative_to(r)
            return True
        except ValueError:
            pass
        try:
            r_real = os.path.realpath(r) if r.exists() else os.path.normpath(str(r))
        except OSError:
            continue
        if p_real == r_real or p_real.startswith(r_real + os.sep):
            return True
    return False


def is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    h = host.strip().lower()
    if h in {"localhost", "127.0.0.1", "::1", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(h.split("%")[0]).is_loopback
    except ValueError:
        return False


def cors_allow_origins() -> list[str]:
    """Explicit CORS origins (never ``*`` with credentials)."""
    raw = (os.environ.get("LIVEHOUSE_CORS_ORIGINS") or "").strip()
    if raw:
        return [x.strip() for x in raw.split(",") if x.strip()]
    lab = (os.environ.get("LIVEHOUSE_LAB_URL") or "http://127.0.0.1:3000").rstrip("/")
    origins = [lab, "http://127.0.0.1:3000", "http://localhost:3000"]
    # Dedupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for o in origins:
        if o not in seen:
            seen.add(o)
            out.append(o)
    return out


def ops_token_configured() -> str:
    return (
        (os.environ.get("LIVEHOUSE_OPS_TOKEN") or "").strip()
        or (os.environ.get("LIVEHOUSE_INFRA_TOKEN") or "").strip()
    )


def _bearer(authorization: Optional[str]) -> str:
    if not authorization:
        return ""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return authorization.strip()


def require_ops_auth(
    request: Request,
    x_livehouse_ops_token: Optional[str] = Header(default=None, alias="X-Livehouse-Ops-Token"),
    x_luma_token: Optional[str] = Header(default=None, alias="X-Luma-Token"),
    authorization: Optional[str] = Header(default=None),
) -> None:
    """Gate mutating ops / infra routes.

    - If ``LIVEHOUSE_OPS_TOKEN`` / ``LIVEHOUSE_INFRA_TOKEN`` is set → require matching header.
    - Else allow only loopback (or ``LIVEHOUSE_ALLOW_INSECURE_LOCAL=1``).
    """
    expected = ops_token_configured()
    provided = (
        (x_livehouse_ops_token or "").strip()
        or (x_luma_token or "").strip()
        or _bearer(authorization)
    )
    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid or missing ops token")
        return

    client_host = request.client.host if request.client else None
    allow_insecure = (os.environ.get("LIVEHOUSE_ALLOW_INSECURE_LOCAL") or "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }
    if allow_insecure or is_loopback_host(client_host):
        return
    raise HTTPException(
        status_code=401,
        detail="set LIVEHOUSE_OPS_TOKEN for non-local infra/ops access",
    )


def require_ingest_auth(
    request: Request,
    x_luma_token: Optional[str] = Header(default=None, alias="X-Luma-Token"),
) -> None:
    """Fail-closed ingest auth unless explicitly opened for local demos."""
    expected = (os.environ.get("LIVEHOUSE_INGEST_TOKEN") or "").strip()
    provided = (x_luma_token or "").strip()
    if expected:
        if provided != expected:
            raise HTTPException(status_code=401, detail="invalid or missing X-Luma-Token")
        return

    client_host = request.client.host if request.client else None
    allow_insecure = (os.environ.get("LIVEHOUSE_ALLOW_INSECURE_LOCAL") or "").strip() in {
        "1",
        "true",
        "TRUE",
        "yes",
        "YES",
    }
    if allow_insecure or is_loopback_host(client_host):
        logger.warning(
            "ingest auth open (no LIVEHOUSE_INGEST_TOKEN); client=%s",
            client_host,
        )
        return
    raise HTTPException(
        status_code=503,
        detail="LIVEHOUSE_INGEST_TOKEN is not configured",
    )


_BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)


def assert_public_http_url(url: str) -> str:
    """Reject non-http(s) and private/loopback/link-local targets (SSRF guard)."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be http(s)")
    host = (parsed.hostname or "").strip().lower()
    if not host or host in _BLOCKED_HOSTS:
        raise ValueError("url host is not allowed")
    # Block literal IPs in private ranges.
    try:
        ip = ipaddress.ip_address(host)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("url resolves to a non-public address")
        return url
    except ValueError as exc:
        if "non-public" in str(exc) or "not allowed" in str(exc):
            raise
        # Hostname — resolve and check every A/AAAA.
        pass

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"url host could not be resolved: {exc}") from exc
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("url resolves to a non-public address")
    return url


class _SlidingWindowLimiter:
    def __init__(self, *, max_events: int, window_s: float) -> None:
        self._max = max_events
        self._window = window_s
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self._window
            kept = [t for t in self._events.get(key, []) if t >= cutoff]
            if len(kept) >= self._max:
                self._events[key] = kept
                return False
            kept.append(now)
            self._events[key] = kept
            return True


_auth_limiter = _SlidingWindowLimiter(max_events=20, window_s=60.0)


def check_auth_rate_limit(request: Request, *, username: str = "") -> None:
    host = request.client.host if request.client else "unknown"
    key = f"{host}|{username.strip().lower()}"
    if not _auth_limiter.allow(key):
        raise HTTPException(status_code=429, detail="too many auth attempts; try again later")


def client_safe_error(exc: BaseException, *, public: str = "internal server error") -> str:
    """Log-friendly: never return raw exception text for unknown failures."""
    if isinstance(exc, (ValueError, FileNotFoundError)):
        msg = str(exc).strip()
        if msg and len(msg) < 200 and "\n" not in msg:
            return msg
    return public
