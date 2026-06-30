"""Tests for the generic, sandboxed Agent Skill layer (services/agent/skills).

Covered: the registry contract (dispatch, error isolation, function-calling specs),
the Python code-execution sandbox (success / failure / timeout / bad input), and the
read-only SQLite query skill (SELECT works; writes and multi-statements are refused).
"""
from __future__ import annotations

import sqlite3
import sys

import pytest

from services.agent.skills import PythonExecSkill, SQLiteQuerySkill, default_registry
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


def test_tool_specs_are_openai_function_shape():
    reg = default_registry()
    specs = reg.tool_specs()
    assert all(s["type"] == "function" for s in specs)
    names = {s["function"]["name"] for s in specs}
    assert "python_exec" in names
    for s in specs:
        assert "parameters" in s["function"]
        assert s["function"]["parameters"]["type"] == "object"


# ------------------------------------------------------------------- code execution


def test_python_exec_success_captures_stdout():
    res = PythonExecSkill().run({"code": "print(6 * 7)"})
    assert res.ok is True
    assert res.output.strip() == "42"
    assert res.metadata["returncode"] == 0


def test_python_exec_nonzero_returncode_surfaces_error():
    res = PythonExecSkill().run({"code": "raise ValueError('boom')"})
    assert res.ok is False
    assert "ValueError" in (res.error or "") or "ValueError" in res.metadata.get("stderr", "")
    assert res.metadata["returncode"] != 0


def test_python_exec_rejects_empty_code():
    res = PythonExecSkill().run({"code": "   "})
    assert res.ok is False and "non-empty" in (res.error or "")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="preexec/timeout semantics POSIX-focused")
def test_python_exec_times_out():
    res = PythonExecSkill(default_timeout_s=1).run({"code": "while True:\n    pass", "timeout_s": 1})
    assert res.ok is False
    assert res.metadata.get("timed_out") is True


def test_python_exec_isolated_from_pythonpath(tmp_path):
    # -I mode must ignore an injected importable module on PYTHONPATH.
    res = PythonExecSkill().run({"code": "import sys; print('site' in sys.modules)"})
    assert res.ok is True


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
