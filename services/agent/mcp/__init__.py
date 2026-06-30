"""Minimal Model Context Protocol (MCP) bridge for the agent's skill layer.

MCP is the open protocol (Anthropic) for exposing *tools / resources / prompts* to an
LLM host over JSON-RPC 2.0. This package is a dependency-free, self-contained subset
that bridges it to the project's existing :class:`~services.agent.skills.base.SkillRegistry`
in BOTH directions:

- **Server** (:class:`McpServer`): renders a ``SkillRegistry`` as MCP ``tools`` and
  answers ``initialize`` / ``tools/list`` / ``tools/call`` — i.e. any MCP host (Claude
  Desktop, an IDE, another agent) can drive our skills unchanged.
- **Client** (:class:`McpClient` + :func:`mcp_tools_as_skills`): talks to a remote MCP
  server and wraps its tools back into local ``Skill`` objects, so a remote server's
  tools plug straight into our planner / ``SkillRegistry`` like any built-in skill.

The transport is injected (a ``request -> response`` callable), so the client and server
compose in-process for tests; :func:`serve_stdio` provides the newline-delimited
JSON-RPC stdio loop a real MCP host launches.
"""
from __future__ import annotations

from services.agent.mcp.client import (
    McpClient,
    in_process_transport,
    mcp_tools_as_skills,
)
from services.agent.mcp.server import (
    MCP_PROTOCOL_VERSION,
    McpError,
    McpServer,
    serve_stdio,
)

__all__ = [
    "MCP_PROTOCOL_VERSION",
    "McpClient",
    "McpError",
    "McpServer",
    "in_process_transport",
    "mcp_tools_as_skills",
    "serve_stdio",
]
