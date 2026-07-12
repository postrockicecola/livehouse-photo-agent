"""Persistent store for agent accounts + conversation memory (SQLite).

This is the durable backbone that turns the copilot from a single-user local tool into
a multi-user product: user accounts (password-hashed), opaque session tokens, and
per-owner conversation history that survives a server restart.

Design notes:

- **Isolated DB.** A dedicated SQLite file (``LIVEHOUSE_AGENT_DB``) keeps auth/chat data
  cleanly separate from the jobs SSOT (``luma_brain.db``). Schema init is idempotent and
  applied once per process per DB path, mirroring ``utils.luma_brain``.
- **No new deps.** Passwords use ``hashlib.pbkdf2_hmac`` (stdlib) with a per-password salt;
  tokens use ``secrets``. Constant-time comparison via ``hmac.compare_digest``.
- **Ownership model.** A conversation is keyed by ``(owner, session_id, mode)`` where
  ``owner`` is ``user:<id>`` for a logged-in user or ``anon:<session_id>`` otherwise, so
  isolation is enforced at the storage layer, not by the caller remembering to filter.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_DB = _REPO_ROOT / "data" / "agent_store.db"

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_MAX_HISTORY_MESSAGES = 40  # cap turns rebuilt into working memory on load

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_tokens (
    token      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_tokens_user ON auth_tokens(user_id);
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
"""

_SCHEMA_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()


def agent_db_path() -> Path:
    raw = os.environ.get("LIVEHOUSE_AGENT_DB", str(_DEFAULT_DB))
    return Path(raw).expanduser().resolve()


def _ensure_schema(conn: sqlite3.Connection, abs_path: str) -> None:
    with _SCHEMA_LOCK:
        if abs_path in _SCHEMA_INITIALIZED:
            return
        conn.executescript(_SCHEMA)
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


# --------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    """Return a ``pbkdf2_sha256$iters$salt_hex$hash_hex`` string for ``password``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iters_s, salt_hex, hash_hex = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(dk, expected)


# ------------------------------------------------------------------------- users


class UserExistsError(Exception):
    """Raised when registering a username that already exists."""


def _user_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": int(row["id"]), "username": str(row["username"]), "created_at": row["created_at"]}


def create_user(conn: sqlite3.Connection, username: str, password: str) -> dict[str, Any]:
    """Create a user; raises :class:`UserExistsError` on duplicate username."""
    username = username.strip()
    try:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
            (username, hash_password(password), time.time()),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise UserExistsError(f"username {username!r} already taken") from exc
    row = conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return _user_public(row)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> Optional[dict[str, Any]]:
    row = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
    if row is None or not verify_password(password, str(row["password_hash"])):
        return None
    return _user_public(row)


# ------------------------------------------------------------------------ tokens


def create_token(conn: sqlite3.Connection, user_id: int, *, ttl: int = _TOKEN_TTL_SECONDS) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO auth_tokens(token, user_id, created_at, expires_at) VALUES(?,?,?,?)",
        (token, int(user_id), now, now + ttl),
    )
    conn.commit()
    return token


def user_for_token(conn: sqlite3.Connection, token: str) -> Optional[dict[str, Any]]:
    """Resolve a bearer token to its user, or ``None`` if invalid/expired."""
    if not token:
        return None
    row = conn.execute(
        "SELECT u.* FROM auth_tokens t JOIN users u ON u.id = t.user_id "
        "WHERE t.token=? AND t.expires_at > ?",
        (token, time.time()),
    ).fetchone()
    return _user_public(row) if row is not None else None


def delete_token(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM auth_tokens WHERE token=?", (token,))
    conn.commit()


def purge_expired_tokens(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM auth_tokens WHERE expires_at <= ?", (time.time(),))
    conn.commit()
    return cur.rowcount or 0


# ----------------------------------------------------------------- conversations


def owner_key(user: Optional[dict[str, Any]], session_id: str) -> str:
    """``user:<id>`` when logged in, else an anonymous key scoped to the session id."""
    if user is not None:
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
        conn.execute("DELETE FROM messages WHERE conversation_id=?", (int(row["id"]),))
        conn.commit()


def message_count(conn: sqlite3.Connection, conversation_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return int(row["n"]) if row else 0
