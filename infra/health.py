"""Health/readiness checks for infra components."""
from __future__ import annotations

import os
import time
from typing import Any

import requests


def db_health() -> dict[str, Any]:
    from utils.luma_brain import brain_connect

    try:
        conn = brain_connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("SELECT 1").fetchone()
            conn.rollback()
            return {"ok": True}
        finally:
            conn.close()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def provider_health(provider: str, endpoint: str) -> dict[str, Any]:
    p = (provider or "ollama").strip().lower()
    if p == "mock":
        return {"ok": True, "provider": p, "detail": "mock provider"}
    base = endpoint.rstrip("/")
    if p in {"vllm", "openai"}:
        if base.endswith("/v1"):
            url = f"{base}/models"
        else:
            url = f"{base}/health"
    else:
        url = f"{base}/api/tags"
    try:
        resp = requests.get(url, timeout=2.5)
        if p in {"vllm", "openai"} and resp.status_code == 404 and not base.endswith("/v1"):
            resp = requests.get(f"{base}/v1/models", timeout=2.5)
        return {
            "ok": resp.status_code == 200,
            "provider": p,
            "status_code": resp.status_code,
            "probe": url,
        }
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
    return {"ok": fresh > 0, "total": total, "fresh": fresh, "fresh_window_seconds": fresh_seconds}


def broker_health() -> dict[str, Any]:
    broker = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    client = None
    try:
        from redis import Redis

        client = Redis.from_url(
            broker,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        ok = bool(client.ping())
        redis_list_len = int(client.llen("celery"))
        return {
            "ok": ok,
            "celery_unavailable": not ok,
            "redis_error": None,
            "redis_list_len": redis_list_len,
        }
    except Exception as exc:
        return {
            "ok": False,
            "celery_unavailable": True,
            "redis_error": str(exc),
            "redis_list_len": None,
        }
    finally:
        if client is not None:
            client.close()


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
