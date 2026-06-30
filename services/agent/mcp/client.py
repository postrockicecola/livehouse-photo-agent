"""MCP client: call a remote MCP server and wrap its tools as local Skills.

The client is transport-agnostic: it is constructed with a ``Transport`` callable that
sends one JSON-RPC request and returns one response (or None). :func:`in_process_transport`
wires it straight to a :class:`~services.agent.mcp.server.McpServer` (used in tests and
for embedding); a real deployment would pass a stdio/HTTP transport instead.

:func:`mcp_tools_as_skills` is the payoff: it turns each remote MCP tool into a local
``Skill`` (same ``name`` / ``description`` / ``parameters`` / ``run`` contract), so a
remote server's tools register into our :class:`SkillRegistry` and are picked by the
planner exactly like a built-in skill — the agent doesn't know or care they live behind
a protocol boundary.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from services.agent.mcp.server import INTERNAL_ERROR, MCP_PROTOCOL_VERSION, McpError, McpServer
from services.agent.skills.base import SkillResult

# Sends one JSON-RPC request, returns one response dict (or None for no reply).
Transport = Callable[[dict[str, Any]], Optional[dict[str, Any]]]


def in_process_transport(server: McpServer) -> Transport:
    """A transport that dispatches straight into an in-process :class:`McpServer`."""
    return server.handle


class McpClient:
    """A small MCP client: handshake, list tools, call a tool, over any transport."""

    def __init__(
        self,
        transport: Transport,
        *,
        name: str = "livehouse-agent",
        version: str = "0.1.0",
    ) -> None:
        self._transport = transport
        self._name = name
        self._version = version
        self._next_id = 0

    def _rpc(self, method: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        self._next_id += 1
        request = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
        response = self._transport(request)
        if response is None:
            raise McpError(INTERNAL_ERROR, f"no response to {method!r}")
        if "error" in response and response["error"]:
            err = response["error"]
            raise McpError(int(err.get("code", INTERNAL_ERROR)), str(err.get("message", "")))
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def initialize(self) -> dict[str, Any]:
        return self._rpc(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self._name, "version": self._version},
            },
        )

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list")
        tools = result.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})


class _RemoteMcpSkill:
    """Adapts one remote MCP tool to the local ``Skill`` protocol (name/desc/params/run)."""

    def __init__(self, client: McpClient, spec: dict[str, Any]) -> None:
        self.name = str(spec.get("name") or "")
        self.description = str(spec.get("description") or "")
        self.parameters = spec.get("inputSchema") or {"type": "object", "properties": {}}
        self._client = client

    def run(self, args: dict[str, Any]) -> SkillResult:
        try:
            result = self._client.call_tool(self.name, args)
        except McpError as exc:
            return SkillResult(ok=False, error=f"mcp call failed: {exc.message}")
        content = result.get("content") or []
        text = "\n".join(
            str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
        is_error = bool(result.get("isError"))
        return SkillResult(
            ok=not is_error,
            output="" if is_error else text,
            error=text if is_error else None,
            metadata={"via": "mcp", "tool": self.name},
        )


def mcp_tools_as_skills(client: McpClient) -> list[_RemoteMcpSkill]:
    """Discover a remote server's tools and wrap each as a local ``Skill``.

    Register the results into a :class:`SkillRegistry` to make remote MCP tools callable
    by the agent exactly like built-in skills::

        client = McpClient(in_process_transport(server)); client.initialize()
        for skill in mcp_tools_as_skills(client):
            registry.register(skill)
    """
    return [_RemoteMcpSkill(client, spec) for spec in client.list_tools()]
