"""General-purpose, sandboxed Agent Skills (tools) the agent can call.

Unlike ``services/agent/tools.py`` (curation-specific tools bound to the photo
``AgentState``), this package is a *generic* skill layer: each skill declares an
OpenAI-style JSON-schema signature and runs in isolation, so the same registry can
back a code-execution sandbox, a read-only DB query tool, search, etc. The registry
emits standard ``tools`` specs (``{"type": "function", "function": {...}}``) so it
plugs straight into OpenAI / vLLM function-calling and frameworks that speak it.
"""
from __future__ import annotations

import os
import re

from services.agent.skills.artifacts import WriteArtifactSkill
from services.agent.skills.base import Skill, SkillRegistry, SkillResult
from services.agent.skills.code_execution import PythonExecSkill
from services.agent.skills.database import SQLiteQuerySkill
from services.agent.skills.web import WebFetchSkill, WebSearchSkill

__all__ = [
    "Skill",
    "SkillRegistry",
    "SkillResult",
    "PythonExecSkill",
    "SQLiteQuerySkill",
    "WebSearchSkill",
    "WebFetchSkill",
    "WriteArtifactSkill",
    "default_registry",
    "general_registry",
    "agent_workspace_root",
]

_SAFE_SESSION = re.compile(r"[^A-Za-z0-9._-]+")


def default_registry(*, db_path: str | None = None) -> SkillRegistry:
    """A registry pre-loaded with the built-in skills (code exec + optional DB)."""
    reg = SkillRegistry()
    reg.register(PythonExecSkill())
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


def general_registry(session_id: str) -> SkillRegistry:
    """A general-purpose agent toolset: web search + fetch, sandboxed code, artifacts.

    Artifacts are written under a per-session workspace and served back via
    ``/api/agent/artifacts/<session>/<name>`` (see ``api/agent_routes.py``).
    """
    safe = safe_session_id(session_id)
    session_dir = os.path.join(agent_workspace_root(), safe)
    reg = SkillRegistry()
    reg.register(WebSearchSkill())
    reg.register(WebFetchSkill())
    reg.register(PythonExecSkill())
    reg.register(WriteArtifactSkill(session_dir, url_prefix=f"/api/agent/artifacts/{safe}"))
    return reg
