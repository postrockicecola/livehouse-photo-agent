"""MCP server: expose a :class:`SkillRegistry` over JSON-RPC 2.0.

Implements the tools slice of the Model Context Protocol — ``initialize``,
``tools/list``, ``tools/call`` (plus ``ping`` and the ``notifications/initialized``
notification) — so a standard MCP host can discover and invoke the project's skills.

The mapping is mechanical: the registry already produces OpenAI-style function specs
(``{"type":"function","function":{name,description,parameters}}``); MCP wants
``{name, description, inputSchema}`` for ``tools/list`` and returns tool output as a
``content`` array for ``tools/call``. A skill error is reported the MCP way — via the
result's ``isError`` flag, not a protocol-level error — so the host can show it to the
model and let it recover (the same "errors don't kill the loop" stance as the registry).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional, TextIO

from services.agent.skills.base import SkillRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"

# JSON-RPC 2.0 standard error codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class McpError(Exception):
    """A JSON-RPC error with a numeric ``code`` (shared by server and client)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpServer:
    """Serve one :class:`SkillRegistry` as an MCP tools endpoint (transport-agnostic)."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        name: str = "livehouse-agent-skills",
        version: str = "0.1.0",
    ) -> None:
        self._registry = registry
        self._name = name
        self._version = version

    # ---------------------------------------------------------------- dispatch

    def handle(self, message: Any) -> Optional[dict[str, Any]]:
        """Process one JSON-RPC message; return a response dict, or None for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, INVALID_REQUEST, "invalid JSON-RPC 2.0 request")
        is_notification = "id" not in message
        mid = message.get("id")
        method = message.get("method")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}

        try:
            if method == "initialize":
                result: dict[str, Any] = self._initialize()
            elif method in ("notifications/initialized", "initialized"):
                return None  # client handshake notification: nothing to reply
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self._tools()}
            elif method == "tools/call":
                result = self._call_tool(params)
            else:
                if is_notification:
                    return None
                return self._error(mid, METHOD_NOT_FOUND, f"method not found: {method!r}")
        except McpError as exc:
            return None if is_notification else self._error(mid, exc.code, exc.message)
        except Exception as exc:  # never leak a stack trace to the wire
            logger.exception("MCP server method %r failed", method)
            return None if is_notification else self._error(mid, INTERNAL_ERROR, f"internal error: {exc}")

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    # ---------------------------------------------------------------- methods

    def _initialize(self) -> dict[str, Any]:
        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self._name, "version": self._version},
        }

    def _tools(self) -> list[dict[str, Any]]:
        """Render the registry as MCP tool descriptors (name / description / inputSchema)."""
        tools: list[dict[str, Any]] = []
        for spec in self._registry.tool_specs():
            fn = spec.get("function") or {}
            tools.append(
                {
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        return tools

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise McpError(INVALID_PARAMS, "tools/call requires a string 'name'")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise McpError(INVALID_PARAMS, "'arguments' must be an object")

        result = self._registry.dispatch(name, arguments)
        text = json.dumps(result.to_observation(), ensure_ascii=False)
        # MCP convention: tool failures surface via isError + a content message, so the
        # host/model can read the error and retry rather than the call faulting the RPC.
        return {"content": [{"type": "text", "text": text}], "isError": not result.ok}

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def serve_stdio(server: McpServer, stdin: TextIO, stdout: TextIO) -> None:
    """Run the newline-delimited JSON-RPC loop an MCP host launches over stdio.

    One JSON object per line in; one response line out (notifications produce no line).
    Malformed lines get a JSON-RPC parse error. Loops until EOF.
    """
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, McpServer._error(None, PARSE_ERROR, "invalid JSON"))
            continue
        response = server.handle(message)
        if response is not None:
            _write(stdout, response)


def _write(stdout: TextIO, obj: dict[str, Any]) -> None:
    stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    stdout.flush()
