"""MCP adapter over the Gallery SkillRegistry (local Cursor).

Consumer: this photographer workstation. Not a second business layer —
``tools/list`` / ``tools/call`` dispatch into ``gallery_registry``.

Exposed surface is smaller than the chat registry (no sqlite, artifacts, or
job-control). Transport: MCP stdio (Content-Length framed JSON-RPC).

Base dir: ``LIVEHOUSE_MCP_BASE_DIR`` / ``LIVEHOUSE_GALLERY_PREVIEWS_DIR``,
else the newest ``latest_session.json`` pointer (same as Gallery).

    python -m services.agent.mcp_gallery
    python scripts/run_mcp_gallery.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, BinaryIO

from services.agent.skills.gallery import gallery_registry

MCP_CONSUMER = "Cursor / Claude Desktop (local photographer workstation)"
MCP_SERVER_NAME = "livehouse-gallery"
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_EXPOSED_TOOLS: tuple[str, ...] = (
    "archive_search",
    "gallery_search",
    "gallery_stats",
    "explain_photo",
    "export_selected",
)


def resolve_mcp_base_dir(*, cwd: str | None = None) -> str | None:
    """Explicit Previews env wins; otherwise newest ``latest_session.json``."""
    for key in (
        "LIVEHOUSE_MCP_BASE_DIR",
        "LIVEHOUSE_GALLERY_DIR",
        "LIVEHOUSE_GALLERY_PREVIEWS_DIR",
    ):
        raw = (os.environ.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        try:
            path = path.resolve()
        except OSError:
            return None
        return str(path) if path.is_dir() else None

    hint = cwd or os.getcwd()
    try:
        from utils.runtime_session import read_newest_latest_session_pointer

        hit = read_newest_latest_session_pointer(base_dir=hint)
    except Exception:
        return None
    if hit is None:
        return None
    _, ref = hit
    previews = Path(str(ref.get("previews_dir") or "")).expanduser()
    try:
        previews = previews.resolve()
    except OSError:
        return None
    return str(previews) if previews.is_dir() else None


def exposed_tool_specs(registry: Any) -> list[dict[str, Any]]:
    """MCP ``tools/list`` entries (name / description / inputSchema)."""
    allowed = set(MCP_EXPOSED_TOOLS)
    tools: list[dict[str, Any]] = []
    for spec in registry.tool_specs():
        fn = spec.get("function") if isinstance(spec.get("function"), dict) else {}
        name = str(fn.get("name") or "").strip()
        if name not in allowed:
            continue
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {}
        tools.append(
            {
                "name": name,
                "description": str(fn.get("description") or ""),
                "inputSchema": params or {"type": "object", "properties": {}},
            }
        )
    return tools


def call_exposed_tool(registry: Any, name: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    tool = str(name or "").strip()
    if tool not in MCP_EXPOSED_TOOLS:
        return {"ok": False, "error": f"tool not exposed over MCP: {tool}"}
    result = registry.dispatch(tool, dict(args or {}))
    return result.to_observation()


def handle_jsonrpc(registry: Any, request: dict[str, Any]) -> dict[str, Any] | None:
    """Return a JSON-RPC response, or ``None`` for notifications (no reply)."""
    req_id = request.get("id")
    method = str(request.get("method") or "")
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        client_ver = str(params.get("protocolVersion") or "").strip()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_ver or MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": MCP_SERVER_NAME, "version": "1"},
                "capabilities": {"tools": {}},
                "consumer": MCP_CONSUMER,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method in ("tools/list", "list_tools"):
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": exposed_tool_specs(registry)}}
    if method in ("tools/call", "call_tool"):
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        observation = call_exposed_tool(registry, name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(observation, ensure_ascii=False)}],
                "isError": not bool(observation.get("ok")),
            },
        }
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"unknown method: {method}"},
    }


def read_stdio_message(reader: BinaryIO) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = reader.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        try:
            decoded = line.decode("ascii")
        except UnicodeDecodeError:
            decoded = line.decode("utf-8", errors="replace")
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    try:
        length = int(headers.get("content-length") or "0")
    except ValueError:
        return None
    if length <= 0:
        return None
    body = reader.read(length)
    if len(body) < length:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def write_stdio_message(writer: BinaryIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    writer.write(body)
    writer.flush()


def main(argv: list[str] | None = None) -> int:
    _ = argv
    base = resolve_mcp_base_dir()
    if not base:
        print(
            "No gallery session: set LIVEHOUSE_MCP_BASE_DIR or write latest_session.json",
            file=sys.stderr,
        )
        return 2
    print(f"{MCP_SERVER_NAME} MCP base_dir={base}", file=sys.stderr)
    registry = gallery_registry(base)
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        request = read_stdio_message(stdin)
        if request is None:
            break
        try:
            response = handle_jsonrpc(registry, request)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "error": {"code": -32603, "message": f"internal error: {exc}"},
            }
        if response is not None:
            write_stdio_message(stdout, response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
