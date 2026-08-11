from __future__ import annotations

import json

import pytest

from services.agent import store
from services.agent.context_governance import format_selection_history_for_prompt
from services.agent.conversation import ConversationalAgent
from services.agent.skills.base import SkillResult


def test_three_selection_turns_append_history_and_keep_last_files_alias() -> None:
    agent = ConversationalAgent(lambda _messages: "ok")

    for turn in range(1, 4):
        files = [f"turn_{turn}_a.jpg", f"turn_{turn}_b.jpg"]
        agent._reset_turn_state()
        agent._update_working_memory(
            "gallery_search",
            {"query": f"query {turn}"},
            SkillResult(ok=True, metadata={"files": files}),
        )
        agent._update_working_memory(
            "gallery_select",
            {"files": files},
            SkillResult(ok=True, metadata={"selected_keys": files}),
        )

    history = agent.working_memory["selection_history"]
    assert len(history) == 3
    assert [row["selection_id"] for row in history] == [
        "sel_001",
        "sel_002",
        "sel_003",
    ]
    assert [row["query"] for row in history] == ["query 1", "query 2", "query 3"]
    assert agent.working_memory["active_selection_id"] == "sel_003"
    assert agent.working_memory["last_files"] == ["turn_3_a.jpg", "turn_3_b.jpg"]


def test_selection_history_prompt_contains_turn_index_and_active_selection() -> None:
    block = format_selection_history_for_prompt(
        {
            "selection_history": [
                {
                    "turn_id": 2,
                    "selection_id": "sel_002",
                    "query": "吉他手高光",
                    "files": ["a.jpg"],
                    "created_at": "2026-08-11T00:00:00+00:00",
                },
                {
                    "turn_id": 3,
                    "selection_id": "sel_003",
                    "query": "最有张力的吉他手",
                    "files": ["b.jpg", "c.jpg"],
                    "created_at": "2026-08-11T00:01:00+00:00",
                },
            ],
            "active_selection_id": "sel_003",
        }
    )

    assert 'Turn 2 (sel_002): "吉他手高光" -> [a.jpg]' in block
    assert 'Turn 3 (sel_003): "最有张力的吉他手" -> [b.jpg, c.jpg]' in block
    assert "Current Active Selection: Turn 3 (sel_003)" in block
    assert "第X轮" in block


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_AGENT_DB", str(tmp_path / "agent_store.db"))
    connection = store.store_connect()
    try:
        yield connection
    finally:
        connection.close()


def test_legacy_working_memory_is_upgraded_when_loaded(conn) -> None:
    cid = store.get_or_create_conversation(conn, "anon:legacy", "session", "gallery")
    legacy = {
        "last_tool": "gallery_search",
        "last_query": "历史吉他手",
        "last_files": ["old_a.jpg", "old_b.jpg"],
    }
    conn.execute(
        "UPDATE conversations SET working_memory=? WHERE id=?",
        (json.dumps(legacy, ensure_ascii=False), cid),
    )
    conn.commit()

    loaded = store.get_working_memory(conn, cid)

    assert loaded["last_files"] == legacy["last_files"]
    assert loaded["active_selection_id"] == "legacy"
    assert loaded["selection_history"] == [
        {
            "turn_id": 1,
            "selection_id": "legacy",
            "query": "历史吉他手",
            "files": ["old_a.jpg", "old_b.jpg"],
            "created_at": "",
        }
    ]
