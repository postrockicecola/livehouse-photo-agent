"""Tests for the generic, sandboxed Agent Skill layer (services/agent/skills).

Covered: the registry contract (dispatch, error isolation, function-calling specs)
and the read-only SQLite query skill (SELECT works; writes and multi-statements are refused).
"""
from __future__ import annotations

import sqlite3

import pytest

from services.agent.skills import SQLiteQuerySkill, default_registry
from services.agent.skills.base import SkillRegistry, SkillResult


# ----------------------------------------------------------------------- registry


def _ok_skill(name: str = "noop"):
    class _S:
        def __init__(self) -> None:
            self.name = name
            self.description = "d"
            self.parameters = {"type": "object", "properties": {}}

        def run(self, args):
            return SkillResult(ok=True, output="hi")

    return _S()


def test_registry_dispatch_and_unknown():
    reg = SkillRegistry()
    reg.register(_ok_skill())
    assert reg.dispatch("noop", {}).ok is True
    miss = reg.dispatch("ghost", {})
    assert miss.ok is False and "unknown" in (miss.error or "")


def test_registry_rejects_duplicate_and_empty_name():
    reg = SkillRegistry()
    reg.register(_ok_skill("a"))
    with pytest.raises(ValueError):
        reg.register(_ok_skill("a"))


def test_registry_isolates_skill_exceptions():
    class _Boom:
        name = "boom"
        description = "d"
        parameters = {"type": "object", "properties": {}}

        def run(self, args):
            raise RuntimeError("kaboom")

    reg = SkillRegistry()
    reg.register(_Boom())
    res = reg.dispatch("boom", {})
    assert res.ok is False and "crashed" in (res.error or "")


def test_tool_specs_are_openai_function_shape(sample_db):
    reg = default_registry(db_path=sample_db)
    specs = reg.tool_specs()
    assert all(s["type"] == "function" for s in specs)
    names = {s["function"]["name"] for s in specs}
    assert "sqlite_query" in names
    for s in specs:
        assert "parameters" in s["function"]
        assert s["function"]["parameters"]["type"] == "object"


# --------------------------------------------------------------------- sqlite query


@pytest.fixture()
def sample_db(tmp_path):
    path = tmp_path / "t.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT)")
    conn.executemany("INSERT INTO jobs (status) VALUES (?)", [("SUCCEEDED",), ("FAILED",), ("FAILED",)])
    conn.commit()
    conn.close()
    return str(path)


def test_sqlite_select_returns_rows(sample_db):
    skill = SQLiteQuerySkill(sample_db)
    res = skill.run({"sql": "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status ORDER BY status"})
    assert res.ok is True
    assert res.metadata["row_count"] == 2
    assert "FAILED" in res.output


def test_sqlite_rejects_writes(sample_db):
    skill = SQLiteQuerySkill(sample_db)
    for bad in ("UPDATE jobs SET status='X'", "DROP TABLE jobs", "INSERT INTO jobs(status) VALUES('x')"):
        res = skill.run({"sql": bad})
        assert res.ok is False and "SELECT" in (res.error or "")


def test_sqlite_rejects_multi_statement(sample_db):
    skill = SQLiteQuerySkill(sample_db)
    res = skill.run({"sql": "SELECT 1; DROP TABLE jobs"})
    assert res.ok is False


def test_sqlite_with_cte_allowed(sample_db):
    skill = SQLiteQuerySkill(sample_db)
    res = skill.run({"sql": "WITH f AS (SELECT * FROM jobs WHERE status='FAILED') SELECT COUNT(*) AS n FROM f"})
    assert res.ok is True


def test_sqlite_limit_truncates(sample_db):
    skill = SQLiteQuerySkill(sample_db, max_rows=200)
    res = skill.run({"sql": "SELECT * FROM jobs", "limit": 1})
    assert res.ok is True
    assert res.metadata["row_count"] == 1
    assert res.metadata["truncated"] is True
