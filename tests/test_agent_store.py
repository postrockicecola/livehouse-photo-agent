"""Offline tests for the agent persistence store (conversation memory)."""
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


# ----------------------------------------------------------------- conversations


def test_owner_key_isolation():
    assert store.owner_key({"id": 7}, "sess") == "user:7"
    assert store.owner_key(None, "sess-abc") == "anon:sess-abc"


def test_schema_has_no_auth_tables(conn):
    names = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "users" not in names
    assert "auth_tokens" not in names
    assert "conversations" in names
    assert "preferences" in names


def test_conversation_persist_and_load(conn):
    owner = "anon:sess-1"
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
    a = store.get_or_create_conversation(conn, "anon:a", "a", "gallery")
    b = store.get_or_create_conversation(conn, "anon:b", "b", "gallery")
    assert a != b
    store.append_messages(conn, a, [{"role": "user", "content": "secret-a"}])
    assert store.message_count(conn, a) == 1
    assert store.message_count(conn, b) == 0  # different owner cannot see it


def test_mode_isolation(conn):
    g = store.get_or_create_conversation(conn, "anon:s", "s", "gallery")
    n = store.get_or_create_conversation(conn, "anon:s", "s", "general")
    assert g != n


def test_reset_conversation_clears_messages(conn):
    cid = store.get_or_create_conversation(conn, "anon:s", "s", "gallery")
    store.append_messages(conn, cid, [{"role": "user", "content": "a"}])
    store.reset_conversation(conn, "anon:s", "s", "gallery")
    assert store.message_count(conn, cid) == 0


def test_load_messages_caps_and_orders(conn):
    cid = store.get_or_create_conversation(conn, "anon:s", "s", "gallery")
    store.append_messages(conn, cid, [{"role": "user", "content": f"m{i}"} for i in range(60)])
    msgs = store.load_messages(conn, cid, limit=10)
    assert len(msgs) == 10
    # Oldest-first slice of the most-recent 10 → m50..m59.
    assert msgs[0]["content"] == "m50" and msgs[-1]["content"] == "m59"


def test_preferences_survive_conversation_reset(conn):
    owner = "anon:9"
    store.set_preference(conn, owner, "avoid_silhouettes", "true")
    store.set_preference(conn, owner, "language", "zh")
    assert store.get_preferences(conn, owner) == {
        "avoid_silhouettes": "true",
        "language": "zh",
    }
    cid = store.get_or_create_conversation(conn, owner, "s", "gallery")
    store.append_messages(conn, cid, [{"role": "user", "content": "hi"}])
    store.reset_conversation(conn, owner, "s", "gallery")
    assert store.message_count(conn, cid) == 0
    assert store.get_preferences(conn, owner)["avoid_silhouettes"] == "true"
    assert "avoid_silhouettes" in store.preferences_prompt_block(store.get_preferences(conn, owner))


def test_agent_events_roundtrip(conn):
    cid = store.get_or_create_conversation(conn, "anon:1", "s", "gallery")
    store.append_agent_events(
        conn,
        cid,
        [
            {"type": "tool_call", "tool": "gallery_search", "ok": True},
            {"type": "done", "reply": "ok"},
        ],
    )
    events = store.load_agent_events(conn, cid)
    assert [e["type"] for e in events] == ["tool_call", "done"]
    assert events[0]["tool"] == "gallery_search"


def test_working_memory_persists_and_clears_on_reset(conn):
    owner = "anon:1"
    cid = store.get_or_create_conversation(conn, owner, "s", "gallery")
    files = [f"keep_{i}.jpg" for i in range(40)]
    store.set_working_memory(
        conn,
        cid,
        {"last_tool": "gallery_search", "last_files": files, "last_query": "交片"},
    )
    wm = store.get_working_memory(conn, cid)
    assert wm["last_tool"] == "gallery_search"
    assert len(wm["last_files"]) == 40
    assert wm["last_files"][0] == "keep_0.jpg"

    store.reset_conversation(conn, owner, "s", "gallery")
    assert store.get_working_memory(conn, cid) == {}


def test_working_memory_from_events_uses_last_search_files():
    events = [
        {
            "type": "tool_call",
            "tool": "gallery_search",
            "args": {"limit": 2},
            "ok": True,
            "metadata": {"files": ["a.jpg", "b.jpg"]},
        },
        {"type": "done", "reply": "ok"},
    ]
    wm = store.working_memory_from_events(events)
    assert wm["last_files"] == ["a.jpg", "b.jpg"]
    assert wm["last_tool"] == "gallery_search"
