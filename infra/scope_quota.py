"""
Per-scope VLM admit budget (Batch E hook).

Keyed by ``(namespace, project_key)`` with an hourly SQLite counter.
Default is **off** (``LIVEHOUSE_SCOPE_VLM_QUOTA_PER_HOUR`` unset or ``0``) so demos
are unchanged. This is not Redis cluster fair-share — interview as a process/SSOT
quota scaffold that can move to a shared token bucket later.
"""
from __future__ import annotations

import os
import time
from typing import Any

from utils.luma_brain import brain_connect


def _quota_limit_per_hour() -> int:
    raw = (os.environ.get("LIVEHOUSE_SCOPE_VLM_QUOTA_PER_HOUR") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _scope_key(namespace: str | None, project_key: str | None) -> str:
    ns = (namespace or "default").strip() or "default"
    pk = (project_key or "default").strip() or "default"
    return f"{ns}\x1f{pk}"


def _ensure_table(conn: Any) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scope_quota_windows (
          scope_key TEXT PRIMARY KEY,
          window_start INTEGER NOT NULL,
          used INTEGER NOT NULL DEFAULT 0,
          updated_at INTEGER NOT NULL
        )
        """
    )


def scope_quota_snapshot(
    *,
    namespace: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    """Read-only remaining budget for metrics (does not consume)."""
    limit = _quota_limit_per_hour()
    key = _scope_key(namespace, project_key)
    hour = int(time.time()) // 3600 * 3600
    if limit <= 0:
        return {
            "enforced": False,
            "limit_per_hour": 0,
            "used": 0,
            "remaining": None,
            "window_start": hour,
            "scope_key": key,
            "authority": "disabled",
        }
    conn = brain_connect()
    try:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT window_start, used FROM scope_quota_windows WHERE scope_key = ?",
            (key,),
        ).fetchone()
        used = 0
        if row is not None and int(row["window_start"] or 0) == hour:
            used = int(row["used"] or 0)
        return {
            "enforced": True,
            "limit_per_hour": limit,
            "used": used,
            "remaining": max(0, limit - used),
            "window_start": hour,
            "scope_key": key,
            "authority": "sqlite_scope_quota_windows",
        }
    finally:
        conn.close()


def admit_vlm_for_scope(
    *,
    namespace: str | None = None,
    project_key: str | None = None,
    cost: int = 1,
) -> dict[str, Any]:
    """
    Consume ``cost`` admits for the current hour window.

    Returns ``{"ok": True/False, ...}``. When enforcement is disabled, always ok.
    """
    limit = _quota_limit_per_hour()
    key = _scope_key(namespace, project_key)
    hour = int(time.time()) // 3600 * 3600
    units = max(1, int(cost))
    if limit <= 0:
        return {
            "ok": True,
            "enforced": False,
            "limit_per_hour": 0,
            "used": 0,
            "remaining": None,
            "scope_key": key,
        }

    conn = brain_connect()
    try:
        _ensure_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT window_start, used FROM scope_quota_windows WHERE scope_key = ?",
                (key,),
            ).fetchone()
            now = int(time.time())
            if row is None or int(row["window_start"] or 0) != hour:
                used = 0
                conn.execute(
                    """
                    INSERT INTO scope_quota_windows (scope_key, window_start, used, updated_at)
                    VALUES (?, ?, 0, ?)
                    ON CONFLICT(scope_key) DO UPDATE SET
                      window_start = excluded.window_start,
                      used = 0,
                      updated_at = excluded.updated_at
                    """,
                    (key, hour, now),
                )
            else:
                used = int(row["used"] or 0)
            if used + units > limit:
                conn.commit()
                return {
                    "ok": False,
                    "enforced": True,
                    "limit_per_hour": limit,
                    "used": used,
                    "remaining": max(0, limit - used),
                    "scope_key": key,
                    "error": "scope_vlm_quota_exceeded",
                }
            used2 = used + units
            conn.execute(
                """
                UPDATE scope_quota_windows
                SET used = ?, updated_at = ?
                WHERE scope_key = ?
                """,
                (used2, now, key),
            )
            conn.commit()
            return {
                "ok": True,
                "enforced": True,
                "limit_per_hour": limit,
                "used": used2,
                "remaining": max(0, limit - used2),
                "scope_key": key,
            }
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()
