"""Tests for the MCP bridge (services/agent/mcp).

Covered behaviors:
- McpServer answers initialize / tools/list / tools/call against a SkillRegistry;
- skill failures surface via MCP isError (not a protocol error);
- unknown method → JSON-RPC method-not-found; notifications get no reply;
- McpClient does the handshake + lists + calls tools over an in-process transport;
- mcp_tools_as_skills wraps remote tools back into Skills that register + dispatch
  through a SkillRegistry (the round trip: registry → server → client → registry);
- serve_stdio runs the newline-delimited JSON-RPC loop.
"""
from __future__ import annotations

import io
import json

from services.agent.mcp import (
    McpClient,
    McpServer,
    in_process_transport,
    mcp_tools_as_skills,
    serve_stdio,
)
from services.agent.mcp.server import METHOD_NOT_FOUND
from services.agent.skills.base import SkillRegistry, SkillResult


class _EchoSkill:
    name = "echo"
    description = "Echo the 'text' argument back."
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    def run(self, args):
        return SkillResult(ok=True, output=str(args.get("text", "")), metadata={"len": len(str(args.get("text", "")))})


class _BoomSkill:
    name = "boom"
    description = "Always fails."
    parameters = {"type": "object", "properties": {}}

    def run(self, args):
        return SkillResult(ok=False, error="kaboom")


def _registry() -> SkillRegistry:
    reg = SkillRegistry()
    reg.register(_EchoSkill())
    reg.register(_BoomSkill())
    return reg


def _rpc(method, params=None, mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}


# --------------------------------------------------------------------- server


def test_server_initialize_and_tools_list():
    server = McpServer(_registry())
    init = server.handle(_rpc("initialize"))
    assert init["result"]["serverInfo"]["name"] == "livehouse-agent-skills"
    assert "tools" in init["result"]["capabilities"]

    listed = server.handle(_rpc("tools/list"))
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"echo", "boom"}
    echo = next(t for t in listed["result"]["tools"] if t["name"] == "echo")
    assert echo["inputSchema"]["required"] == ["text"]  # mapped from function parameters


def test_server_tools_call_success_and_error():
    server = McpServer(_registry())
    ok = server.handle(_rpc("tools/call", {"name": "echo", "arguments": {"text": "hi"}}))
    assert ok["result"]["isError"] is False
    payload = json.loads(ok["result"]["content"][0]["text"])
    assert payload["output"] == "hi"

    bad = server.handle(_rpc("tools/call", {"name": "boom", "arguments": {}}))
    assert bad["result"]["isError"] is True  # skill failure → isError, not RPC error

    unknown = server.handle(_rpc("tools/call", {"name": "nope"}))
    assert unknown["result"]["isError"] is True  # unknown tool also reported via isError


def test_server_unknown_method_and_notification():
    server = McpServer(_registry())
    err = server.handle(_rpc("does/not/exist"))
    assert err["error"]["code"] == METHOD_NOT_FOUND

    # a notification (no id) never gets a reply
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "tools/list"}) is None


def test_server_rejects_invalid_jsonrpc():
    server = McpServer(_registry())
    resp = server.handle({"id": 1, "method": "tools/list"})  # missing jsonrpc
    assert resp["error"]["code"] == -32600


# --------------------------------------------------------------------- client


def test_client_roundtrip_over_in_process_transport():
    server = McpServer(_registry())
    client = McpClient(in_process_transport(server))
    init = client.initialize()
    assert init["protocolVersion"]

    tools = client.list_tools()
    assert {t["name"] for t in tools} == {"echo", "boom"}

    result = client.call_tool("echo", {"text": "yo"})
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])["output"] == "yo"


def test_remote_tools_register_as_skills_and_dispatch():
    # Round trip: a registry served over MCP, consumed by a client, wrapped back into
    # Skills, and registered into a *fresh* registry that dispatches them transparently.
    server = McpServer(_registry())
    client = McpClient(in_process_transport(server))
    client.initialize()

    local = SkillRegistry()
    for skill in mcp_tools_as_skills(client):
        local.register(skill)

    assert set(local.names()) == {"echo", "boom"}
    # function-calling specs survive the bridge (so a planner LLM can pick them)
    assert any(s["function"]["name"] == "echo" for s in local.tool_specs())

    ok = local.dispatch("echo", {"text": "bridged"})
    # The MCP content carries the remote skill's full observation (JSON), so the bridged
    # output embeds the original "bridged" text rather than being exactly equal to it.
    assert ok.ok and "bridged" in ok.output
    assert ok.metadata["via"] == "mcp"

    boom = local.dispatch("boom", {})
    assert boom.ok is False and "kaboom" in (boom.error or "")


# --------------------------------------------------------------------- stdio


def test_serve_stdio_loop():
    server = McpServer(_registry())
    stdin = io.StringIO(
        json.dumps(_rpc("tools/list", mid=1)) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"  # no reply
        + json.dumps(_rpc("tools/call", {"name": "echo", "arguments": {"text": "x"}}, mid=2)) + "\n"
    )
    stdout = io.StringIO()
    serve_stdio(server, stdin, stdout)

    lines = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(lines) == 2  # the notification produced no output line
    assert lines[0]["id"] == 1 and "tools" in lines[0]["result"]
    assert lines[1]["id"] == 2 and lines[1]["result"]["isError"] is False
