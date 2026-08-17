"""MCP adapter over the Gallery SkillRegistry (Cursor / Claude Desktop).

Consumer: local photographer workstation (Cursor). Not a second business layer —
``tools/list`` and ``tools/call`` dispatch into ``gallery_registry``.

Exposed surface is intentionally smaller than the chat registry (no sqlite,
artifacts, or job-control tools). Transport: newline-delimited JSON-RPC on stdio.

    LIVEHOUSE_MCP_BASE_DIR=/path/to/Previews python -m services.agent.mcp_gallery
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from services.agent.skills.gallery import gallery_registry

MCP_CONSUMER = "Cursor / Claude Desktop (local photographer workstation)"
MCP_EXPOSED_TOOLS: tuple[str, ...] = (
    "archive_search",
    "gallery_search",
    "gallery_stats",
    "explain_photo",
    "export_selected",
)


def exposed_tool_specs(registry: Any) -> list[dict[str, Any]]:
    allowed = set(MCP_EXPOSED_TOOLS)
    return [spec for spec in registry.tool_specs() if spec.get("function", {}).get("name") in allowed]


def call_exposed_tool(registry: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = str(name or "").strip()
    if tool not in MCP_EXPOSED_TOOLS:
        return {"ok": False, "error": f"tool not exposed over MCP: {tool}"}
    result = registry.dispatch(tool, dict(args or {}))
    return result.to_observation()


def handle_jsonrpc(registry: Any, request: dict[str, Any]) -> dict[str, Any]:
    req_id = request.get("id")
    method = str(request.get("method") or "")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method in ("initialize", "notifications/initialized"):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "livehouse-gallery", "version": "1"},
                "capabilities": {"tools": {}},
                "consumer": MCP_CONSUMER,
            },
        }
    if method in ("tools/list", "list_tools"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": exposed_tool_specs(registry)}}
    if method in ("tools/call", "call_tool"):
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        observation = call_exposed_tool(registry, name, arguments)
        text = json.dumps(observation, ensure_ascii=False)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": not bool(observation.get("ok")),
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def main(argv: list[str] | None = None) -> int:
    _ = argv
    base = (
        os.environ.get("LIVEHOUSE_MCP_BASE_DIR")
        or os.environ.get("LIVEHOUSE_GALLERY_DIR")
        or ""
    ).strip()
    if not base:
        print("LIVEHOUSE_MCP_BASE_DIR is required", file=sys.stderr)
        return 2
    registry = gallery_registry(base)
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"parse error: {exc}"},
            }
        else:
            if not isinstance(request, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "request must be an object"},
                }
            else:
                response = handle_jsonrpc(registry, request)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
