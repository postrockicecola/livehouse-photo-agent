"""Offline tests for the agent persistence store (accounts + conversation memory)."""
from __future__ import annotations

import pytest

from services.agent import store


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_AGENT_DB", str(tmp_path / "agent_store.db"))
    c = store.store_connect()
    try:
        yield c
    finally:
        c.close()


# --------------------------------------------------------------------- passwords


def test_password_hash_roundtrip():
    h = store.hash_password("s3cret!")
    assert h.startswith("pbkdf2_sha256$")
    assert store.verify_password("s3cret!", h) is True
    assert store.verify_password("wrong", h) is False


def test_verify_password_rejects_malformed():
    assert store.verify_password("x", "not-a-hash") is False
    assert store.verify_password("x", "") is False


# ------------------------------------------------------------------------- users


def test_create_and_authenticate_user(conn):
    user = store.create_user(conn, "alice", "pw12345")
    assert user["username"] == "alice" and user["id"] > 0
    assert store.authenticate(conn, "alice", "pw12345")["id"] == user["id"]
    assert store.authenticate(conn, "alice", "nope") is None
    assert store.authenticate(conn, "ghost", "pw12345") is None


def test_duplicate_username_rejected(conn):
    store.create_user(conn, "bob", "pw")
    with pytest.raises(store.UserExistsError):
        store.create_user(conn, "bob", "other")


# ------------------------------------------------------------------------ tokens


def test_token_resolves_to_user(conn):
    user = store.create_user(conn, "carol", "pw")
    token = store.create_token(conn, user["id"])
    assert store.user_for_token(conn, token)["id"] == user["id"]
    assert store.user_for_token(conn, "bogus") is None
    store.delete_token(conn, token)
    assert store.user_for_token(conn, token) is None


def test_expired_token_is_invalid(conn):
    user = store.create_user(conn, "dave", "pw")
    token = store.create_token(conn, user["id"], ttl=-1)  # already expired
    assert store.user_for_token(conn, token) is None
    assert store.purge_expired_tokens(conn) >= 1


# ----------------------------------------------------------------- conversations


def test_owner_key_isolation():
    assert store.owner_key({"id": 7}, "sess") == "user:7"
    assert store.owner_key(None, "sess-abc") == "anon:sess-abc"


def test_conversation_persist_and_load(conn):
    owner = "user:1"
    cid = store.get_or_create_conversation(conn, owner, "sess-1", "gallery")
    # Idempotent: same key returns the same conversation id.
    assert store.get_or_create_conversation(conn, owner, "sess-1", "gallery") == cid
    store.append_messages(conn, cid, [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ])
    msgs = store.load_messages(conn, cid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hi"
    assert store.message_count(conn, cid) == 2


def test_conversation_owner_isolation(conn):
    a = store.get_or_create_conversation(conn, "user:1", "s", "gallery")
    b = store.get_or_create_conversation(conn, "user:2", "s", "gallery")
    assert a != b
    store.append_messages(conn, a, [{"role": "user", "content": "secret-a"}])
    assert store.message_count(conn, a) == 1
    assert store.message_count(conn, b) == 0  # different owner cannot see it


def test_mode_isolation(conn):
    g = store.get_or_create_conversation(conn, "user:1", "s", "gallery")
    n = store.get_or_create_conversation(conn, "user:1", "s", "general")
    assert g != n


def test_reset_conversation_clears_messages(conn):
    cid = store.get_or_create_conversation(conn, "user:1", "s", "gallery")
    store.append_messages(conn, cid, [{"role": "user", "content": "a"}])
    store.reset_conversation(conn, "user:1", "s", "gallery")
    assert store.message_count(conn, cid) == 0


def test_load_messages_caps_and_orders(conn):
    cid = store.get_or_create_conversation(conn, "user:1", "s", "gallery")
    store.append_messages(conn, cid, [{"role": "user", "content": f"m{i}"} for i in range(60)])
    msgs = store.load_messages(conn, cid, limit=10)
    assert len(msgs) == 10
    # Oldest-first slice of the most-recent 10 → m50..m59.
    assert msgs[0]["content"] == "m50" and msgs[-1]["content"] == "m59"
