"""Read-only SQLite query skill.

Database access is a classic Agent Skill (the JD's "search/database/code execution"),
but letting a model run arbitrary SQL is dangerous. Safety here is enforced two ways:

1. **Read-only connection** — opened via the ``file:...?mode=ro`` URI, so the SQLite
   engine itself rejects any write at the driver level.
2. **Statement allow-list** — only a single ``SELECT`` (or ``WITH ... SELECT``) is
   accepted; multi-statement scripts, PRAGMA, ATTACH, and DML/DDL are refused before
   they reach the engine.

Results are row- and time-capped and returned as JSON-friendly dicts. This pairs with
the read-only intent of the project's own ``luma_brain.db`` (``jobs`` / ``job_events``),
so the skill can answer "how many jobs failed today?" without any write surface.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from services.agent.skills.base import SkillResult

_MAX_ROWS = 200


def _is_single_select(sql: str) -> bool:
    """True iff ``sql`` is exactly one SELECT/CTE statement (no extra statements)."""
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False
    # Reject anything with an embedded statement separator (crude multi-statement guard).
    if ";" in stripped:
        return False
    head = stripped.lstrip("(").split(None, 1)
    if not head:
        return False
    first = head[0].lower()
    return first in ("select", "with")


class SQLiteQuerySkill:
    """Run a single read-only SELECT against a SQLite database file."""

    name = "sqlite_query"
    description = (
        "Run ONE read-only SQL SELECT (or WITH...SELECT) against the configured SQLite "
        "database and return up to a few hundred rows as JSON. Writes, PRAGMA, ATTACH "
        "and multi-statement scripts are rejected."
    )
    parameters = {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "A single SELECT statement."},
            "limit": {"type": "integer", "minimum": 1, "maximum": _MAX_ROWS,
                      "description": f"Max rows to return (default {_MAX_ROWS})."},
        },
        "required": ["sql"],
        "additionalProperties": False,
    }

    def __init__(self, db_path: str, *, timeout_s: float = 5.0, max_rows: int = _MAX_ROWS) -> None:
        self._db_path = db_path
        self._timeout = timeout_s
        self._max_rows = max_rows

    def run(self, args: dict[str, Any]) -> SkillResult:
        sql = args.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            return SkillResult(ok=False, error="'sql' must be a non-empty string")
        if not _is_single_select(sql):
            return SkillResult(ok=False, error="only a single read-only SELECT is allowed")
        try:
            limit = int(args.get("limit") or self._max_rows)
        except (TypeError, ValueError):
            limit = self._max_rows
        limit = max(1, min(self._max_rows, limit))

        uri = f"file:{self._db_path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=self._timeout)
        except sqlite3.Error as exc:
            return SkillResult(ok=False, error=f"cannot open db read-only: {exc}")
        try:
            conn.row_factory = sqlite3.Row
            # Belt-and-suspenders: forbid writes even if the URI is misconfigured.
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(sql)
            rows = cur.fetchmany(limit + 1)
            truncated = len(rows) > limit
            out_rows = [dict(r) for r in rows[:limit]]
            cols = [d[0] for d in cur.description] if cur.description else []
        except sqlite3.Error as exc:
            return SkillResult(ok=False, error=f"query failed: {exc}")
        finally:
            conn.close()

        return SkillResult(
            ok=True,
            output=str(out_rows),
            metadata={"columns": cols, "row_count": len(out_rows), "truncated": truncated},
        )
