"""Persistent store for Gallery Copilot conversation memory (SQLite).

Ownership is **session-scoped anonymity**: conversations are keyed by
``(owner, session_id, mode)`` where ``owner`` is always ``anon:<session_id>``
via :func:`owner_key`. Login / bearer tokens were removed from the product
surface (see ``docs/AGENT_SLIM.txt``); isolation is the secrecy of ``session_id``.

Design notes:

- **Isolated DB.** A dedicated SQLite file (``LIVEHOUSE_AGENT_DB``) keeps chat data
  separate from the jobs SSOT (``luma_brain.db``). Schema init is idempotent.
- **No account tables.** Preferences and events are scoped by the same ``owner``
  string as conversations — no users / auth_tokens schema.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _REPO_ROOT / "data" / "agent_store.db"

_MAX_HISTORY_MESSAGES = 40  # cap turns rebuilt into working memory on load

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    owner      TEXT NOT NULL,
    session_id TEXT NOT NULL,
    mode       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(owner, session_id, mode)
);
CREATE INDEX IF NOT EXISTS idx_conversations_owner ON conversations(owner, updated_at);
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    name            TEXT,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS preferences (
    owner      TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (owner, key)
);
CREATE INDEX IF NOT EXISTS idx_preferences_owner ON preferences(owner);
CREATE TABLE IF NOT EXISTS agent_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_conv ON agent_events(conversation_id, id);
CREATE TABLE IF NOT EXISTS selection_experiences (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner        TEXT NOT NULL,
    tenant       TEXT NOT NULL,
    selection_id TEXT,
    query        TEXT NOT NULL,
    subject      TEXT,
    style        TEXT,
    platform     TEXT,
    decision     TEXT NOT NULL,
    reason_code  TEXT,
    feedback     TEXT NOT NULL,
    files_json   TEXT NOT NULL,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_selection_experiences_owner
ON selection_experiences(owner, tenant, created_at DESC);
"""

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()


def agent_db_path() -> Path:
    raw = os.environ.get("LIVEHOUSE_AGENT_DB", str(_DEFAULT_DB))
    return Path(raw).expanduser().resolve()


def _ensure_schema(conn: sqlite3.Connection, abs_path: str) -> None:
    # Always apply idempotent DDL so additive migrations (preferences, agent_events)
    # land even if this process previously initialized an older schema revision.
    with _SCHEMA_LOCK:
        conn.executescript(_SCHEMA)
        cols = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(conversations)").fetchall()
        }
        if "working_memory" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN working_memory TEXT")
        conn.commit()
        _SCHEMA_INITIALIZED.add(abs_path)


def store_connect() -> sqlite3.Connection:
    """Open a fresh connection (WAL, FK on, Row factory) with schema ensured."""
    path = agent_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    abs_path = str(path.resolve())
    conn = sqlite3.connect(abs_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    _ensure_schema(conn, abs_path)
    return conn


# ----------------------------------------------------------------- conversations


def owner_key(user: Optional[dict[str, Any]], session_id: str) -> str:
    """Return the storage owner for this chat.

    Production always passes ``user=None`` → ``anon:<session_id>``.
    A synthetic ``user`` dict with ``id`` is still accepted for isolation tests
    and any future opt-in auth layer, without requiring account tables.
    """
    if user is not None and user.get("id") is not None:
        return f"user:{int(user['id'])}"
    return f"anon:{session_id}"


def get_or_create_conversation(
    conn: sqlite3.Connection, owner: str, session_id: str, mode: str
) -> int:
    now = time.time()
    row = conn.execute(
        "SELECT id FROM conversations WHERE owner=? AND session_id=? AND mode=?",
        (owner, session_id, mode),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO conversations(owner, session_id, mode, created_at, updated_at) VALUES(?,?,?,?,?)",
        (owner, session_id, mode, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def load_messages(conn: sqlite3.Connection, conversation_id: int, *, limit: int = _MAX_HISTORY_MESSAGES) -> list[dict[str, Any]]:
    """Return the most recent ``limit`` messages for a conversation, oldest-first."""
    rows = conn.execute(
        "SELECT role, content, name FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"], "name": r["name"]} for r in reversed(rows)]


def append_messages(conn: sqlite3.Connection, conversation_id: int, messages: list[dict[str, Any]]) -> None:
    """Append messages (each ``{role, content, name?}``) and bump ``updated_at``."""
    if not messages:
        return
    now = time.time()
    conn.executemany(
        "INSERT INTO messages(conversation_id, role, content, name, created_at) VALUES(?,?,?,?,?)",
        [(conversation_id, m["role"], m.get("content", ""), m.get("name"), now) for m in messages],
    )
    conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
    conn.commit()


def reset_conversation(conn: sqlite3.Connection, owner: str, session_id: str, mode: str) -> None:
    """Delete all messages for a conversation (keeps the conversation row)."""
    row = conn.execute(
        "SELECT id FROM conversations WHERE owner=? AND session_id=? AND mode=?",
        (owner, session_id, mode),
    ).fetchone()
    if row is not None:
        cid = int(row["id"])
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        conn.execute(
            "UPDATE conversations SET working_memory=NULL, updated_at=? WHERE id=?",
            (time.time(), cid),
        )
        conn.commit()


def add_selection_experience(
    conn: sqlite3.Connection,
    *,
    owner: str,
    tenant: str,
    query: str,
    feedback: str,
    decision: str,
    files: list[str],
    selection_id: str = "",
    subject: str = "",
    style: str = "",
    platform: str = "",
    reason_code: str = "",
) -> int:
    """Persist one owner-scoped selection decision for Experience RAG."""
    import json

    cur = conn.execute(
        """
        INSERT INTO selection_experiences(
          owner, tenant, selection_id, query, subject, style, platform,
          decision, reason_code, feedback, files_json, created_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            owner,
            tenant or "default",
            selection_id or None,
            query,
            subject or None,
            style or None,
            platform or None,
            decision,
            reason_code or None,
            feedback,
            json.dumps(files, ensure_ascii=False),
            time.time(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_selection_experiences(
    conn: sqlite3.Connection,
    *,
    owner: str,
    tenant: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return recent experiences visible to exactly one owner/tenant."""
    import json

    rows = conn.execute(
        """
        SELECT id, selection_id, query, subject, style, platform, decision,
               reason_code, feedback, files_json, created_at
        FROM selection_experiences
        WHERE owner=? AND tenant=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (owner, tenant or "default", max(1, min(1000, int(limit)))),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            files = json.loads(row["files_json"] or "[]")
        except (TypeError, json.JSONDecodeError):
            files = []
        out.append(
            {
                "id": int(row["id"]),
                "selection_id": str(row["selection_id"] or ""),
                "query": str(row["query"] or ""),
                "subject": str(row["subject"] or ""),
                "style": str(row["style"] or ""),
                "platform": str(row["platform"] or ""),
                "decision": str(row["decision"] or ""),
                "reason_code": str(row["reason_code"] or ""),
                "feedback": str(row["feedback"] or ""),
                "files": [str(value) for value in files] if isinstance(files, list) else [],
                "created_at": float(row["created_at"] or 0.0),
            }
        )
    return out


def get_working_memory(conn: sqlite3.Connection, conversation_id: int) -> dict[str, Any]:
    """Return the durable short-term working memory for a conversation (may be empty)."""
    import json

    row = conn.execute(
        "SELECT working_memory FROM conversations WHERE id=?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        return {}
    raw = row["working_memory"] if "working_memory" in row.keys() else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    from services.agent.context_governance import compress_working_memory

    return compress_working_memory(data)


def set_working_memory(
    conn: sqlite3.Connection, conversation_id: int, working: dict[str, Any]
) -> None:
    """Persist compact working memory (last search hits, etc.) for the next turn."""
    import json

    from services.agent.context_governance import compress_working_memory

    compact = compress_working_memory(working or {})
    payload = json.dumps(compact, ensure_ascii=False) if compact else None
    conn.execute(
        "UPDATE conversations SET working_memory=?, updated_at=? WHERE id=?",
        (payload, time.time(), conversation_id),
    )
    conn.commit()


def working_memory_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Best-effort rebuild of working memory from recent tool_call / done events."""
    from services.agent.context_governance import compress_working_memory

    for ev in reversed(events or []):
        wm = ev.get("working_memory")
        if isinstance(wm, dict) and (
            wm.get("selection_history") or wm.get("last_files") or wm.get("last_tool")
        ):
            return compress_working_memory(wm)
        if str(ev.get("type") or "") != "tool_call" and "tool" not in ev:
            continue
        meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
        files = meta.get("files") or meta.get("selected_keys")
        if not files:
            continue
        rebuilt: dict[str, Any] = {
            "last_tool": ev.get("tool"),
            "last_files": list(files),
        }
        args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
        if args.get("query") is not None:
            rebuilt["last_query"] = args.get("query")
        if meta.get("citations"):
            rebuilt["last_citations"] = list(meta.get("citations") or [])
        return compress_working_memory(rebuilt)
    return {}


def message_count(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return int(row["n"]) if row else 0


def list_conversations(
    conn: sqlite3.Connection,
    *,
    since_ts: float | None = None,
    until_ts: float | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """List conversations touched in ``[since_ts, until_ts)`` (by ``updated_at``)."""
    clauses: list[str] = []
    params: list[Any] = []
    if since_ts is not None:
        clauses.append("updated_at >= ?")
        params.append(float(since_ts))
    if until_ts is not None:
        clauses.append("updated_at < ?")
        params.append(float(until_ts))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(5000, int(limit))))
    rows = conn.execute(
        f"SELECT id, owner, session_id, mode, created_at, updated_at "
        f"FROM conversations {where} ORDER BY updated_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "owner": r["owner"],
            "session_id": r["session_id"],
            "mode": r["mode"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


# ----------------------------------------------------------------- preferences (long-term)


def set_preference(conn: sqlite3.Connection, owner: str, key: str, value: str) -> None:
    """Upsert a long-term preference for *owner* (survives conversation resets)."""
    k = (key or "").strip()[:120]
    if not k:
        raise ValueError("preference key must be non-empty")
    conn.execute(
        """
        INSERT INTO preferences(owner, key, value, updated_at) VALUES(?,?,?,?)
        ON CONFLICT(owner, key) DO UPDATE SET
          value = excluded.value,
          updated_at = excluded.updated_at
        """,
        (owner, k, str(value)[:2000], time.time()),
    )
    conn.commit()


def get_preferences(conn: sqlite3.Connection, owner: str) -> dict[str, str]:
    rows = conn.execute(
        "SELECT key, value FROM preferences WHERE owner=? ORDER BY key",
        (owner,),
    ).fetchall()
    return {str(r["key"]): str(r["value"]) for r in rows}


def delete_preference(conn: sqlite3.Connection, owner: str, key: str) -> bool:
    cur = conn.execute(
        "DELETE FROM preferences WHERE owner=? AND key=?",
        (owner, (key or "").strip()),
    )
    conn.commit()
    return bool(cur.rowcount)


def preferences_prompt_block(prefs: dict[str, str]) -> str:
    """Format durable preferences for injection into the system prompt."""
    if not prefs:
        return ""
    lines = ["LONG-TERM USER PREFERENCES (honor unless the user overrides this turn):"]
    for k, v in prefs.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


# --------------------------------------------------------------- agent event trace


def _compact_agent_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Keep CTA-critical fields; drop bulky working_memory / full trace from done events."""
    if str(ev.get("type") or "") != "done":
        return ev
    tool_calls: list[dict[str, Any]] = []
    for tc in ev.get("tool_calls") or []:
        if not isinstance(tc, dict) or not tc.get("tool"):
            continue
        meta = dict(tc.get("metadata") or {})
        # UI CTAs need ui_action / session_vibe / files; drop heavy search rows.
        meta.pop("rows", None)
        meta.pop("decision", None)
        tool_calls.append(
            {
                "tool": tc.get("tool"),
                "args": tc.get("args") or {},
                "ok": bool(tc.get("ok")),
                "metadata": meta,
            }
        )
    return {
        "type": "done",
        "reply": ev.get("reply"),
        "tool_calls": tool_calls,
        "routed": ev.get("routed"),
        "backend": ev.get("backend") or (ev.get("trace") or {}).get("backend"),
    }


def append_agent_events(
    conn: sqlite3.Connection,
    conversation_id: int,
    events: list[dict[str, Any]],
) -> None:
    """Persist structured agent step events (tool calls, done) for timeline replay."""
    if not events:
        return
    import json

    now = time.time()
    rows = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        payload = json.dumps(_compact_agent_event(ev), ensure_ascii=False)
        if len(payload) > 48000:
            payload = payload[:48000]
        rows.append((conversation_id, str(ev.get("type") or "event"), payload, now))
    if not rows:
        return
    conn.executemany(
        "INSERT INTO agent_events(conversation_id, event_type, payload, created_at) VALUES(?,?,?,?)",
        rows,
    )
    conn.commit()


def load_agent_events(
    conn: sqlite3.Connection,
    conversation_id: int,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    import json

    rows = conn.execute(
        "SELECT event_type, payload, created_at FROM agent_events "
        "WHERE conversation_id=? ORDER BY id DESC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in reversed(rows):
        try:
            payload = json.loads(r["payload"])
        except json.JSONDecodeError:
            payload = {"raw": r["payload"]}
        if not isinstance(payload, dict):
            payload = {"raw": payload}
        out.append(
            {
                "type": r["event_type"],
                "created_at": r["created_at"],
                **payload,
            }
        )
    return out
