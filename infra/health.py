"""Health/readiness checks for infra components."""
from __future__ import annotations

import time
from typing import Any

import requests

from infra.metrics import queue_backlog_snapshot


def db_health() -> dict[str, Any]:
    from utils.luma_brain import brain_connect

    try:
        conn = brain_connect()
        try:
            conn.execute("SELECT 1").fetchone()
            return {"ok": True}
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def provider_health(provider: str, endpoint: str) -> dict[str, Any]:
    p = (provider or "ollama").strip().lower()
    if p == "mock":
        return {"ok": True, "provider": p, "detail": "mock provider"}
    try:
        resp = requests.get(f"{endpoint.rstrip('/')}/api/tags", timeout=2.5)
        return {"ok": resp.status_code == 200, "provider": p, "status_code": resp.status_code}
    except Exception as exc:
        return {"ok": False, "provider": p, "error": str(exc)}


def worker_freshness(conn: Any, fresh_seconds: int = 120) -> dict[str, Any]:
    now = int(time.time())
    total = int(conn.execute("SELECT COUNT(*) AS c FROM workers").fetchone()["c"])
    fresh = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM workers WHERE last_heartbeat IS NOT NULL AND last_heartbeat >= ?",
            (now - fresh_seconds,),
        ).fetchone()["c"]
    )
    return {"ok": total == 0 or fresh > 0, "total": total, "fresh": fresh, "fresh_window_seconds": fresh_seconds}


def broker_health() -> dict[str, Any]:
    q = queue_backlog_snapshot()
    return {
        "ok": not bool(q.get("celery_unavailable")),
        "celery_unavailable": bool(q.get("celery_unavailable")),
        "redis_error": q.get("redis_error"),
        "redis_list_len": q.get("redis_list_len"),
    }


def health_report(conn: Any, *, provider: str, endpoint: str) -> dict[str, Any]:
    try:
        conn.execute("SELECT 1").fetchone()
        db: dict[str, Any] = {"ok": True}
    except Exception as exc:
        db = {"ok": False, "error": str(exc)}
    broker = broker_health()
    p = provider_health(provider, endpoint)
    workers = worker_freshness(conn)
    ok = all(bool(x.get("ok")) for x in (db, broker, p, workers))
    return {
        "ok": ok,
        "checks": {
            "db": db,
            "broker": broker,
            "provider": p,
            "workers": workers,
        },
    }
