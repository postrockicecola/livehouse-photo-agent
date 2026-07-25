"""General-purpose, sandboxed Agent Skills (tools) the agent can call.

Unlike ``services/agent/tools.py`` (curation-specific tools bound to the photo
``AgentState``), this package is a *generic* skill layer: each skill declares an
OpenAI-style JSON-schema signature and runs in isolation. The registry emits
standard ``tools`` specs (``{"type": "function", "function": {...}}``) so it plugs
straight into OpenAI / vLLM function-calling and frameworks that speak it.
"""
from __future__ import annotations

import os
import re

from services.agent.skills.artifacts import WriteArtifactSkill
from services.agent.skills.base import Skill, SkillRegistry, SkillResult
from services.agent.skills.database import SQLiteQuerySkill

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillResult",
    "SQLiteQuerySkill",
    "WriteArtifactSkill",
    "default_registry",
    "agent_workspace_root",
    "safe_session_id",
]

_SAFE_SESSION = re.compile(r"[^A-Za-z0-9._-]+")


def default_registry(*, db_path: str | None = None) -> SkillRegistry:
    """A registry pre-loaded with optional built-in skills (read-only DB)."""
    reg = SkillRegistry()
    if db_path:
        reg.register(SQLiteQuerySkill(db_path))
    return reg


def agent_workspace_root() -> str:
    """Root dir for per-session agent artifacts (``LIVEHOUSE_AGENT_WORKSPACE`` override)."""
    root = os.environ.get("LIVEHOUSE_AGENT_WORKSPACE")
    if root:
        return os.path.abspath(os.path.expanduser(root))
    return os.path.join(os.getcwd(), "data", "agent_workspace")


def safe_session_id(session_id: str) -> str:
    """Sanitize a client session id into a safe single path segment."""
    s = _SAFE_SESSION.sub("_", str(session_id or "").strip()).strip("._")
    return (s or "default")[:120]
