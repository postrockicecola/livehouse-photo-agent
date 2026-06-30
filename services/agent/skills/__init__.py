"""General-purpose, sandboxed Agent Skills (tools) the agent can call.

Unlike ``services/agent/tools.py`` (curation-specific tools bound to the photo
``AgentState``), this package is a *generic* skill layer: each skill declares an
OpenAI-style JSON-schema signature and runs in isolation, so the same registry can
back a code-execution sandbox, a read-only DB query tool, search, etc. The registry
emits standard ``tools`` specs (``{"type": "function", "function": {...}}``) so it
plugs straight into OpenAI / vLLM function-calling and frameworks that speak it.
"""
from __future__ import annotations

from services.agent.skills.base import Skill, SkillRegistry, SkillResult
from services.agent.skills.code_execution import PythonExecSkill
from services.agent.skills.database import SQLiteQuerySkill

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillResult",
    "PythonExecSkill",
    "SQLiteQuerySkill",
    "default_registry",
]


def default_registry(*, db_path: str | None = None) -> SkillRegistry:
    """A registry pre-loaded with the built-in skills (code exec + optional DB)."""
    reg = SkillRegistry()
    reg.register(PythonExecSkill())
    if db_path:
        reg.register(SQLiteQuerySkill(db_path))
    return reg
