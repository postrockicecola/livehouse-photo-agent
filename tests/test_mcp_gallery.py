"""MCP adapter over gallery_registry — list/call + stdio smoke."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from services.agent.mcp_gallery import (
    MCP_EXPOSED_TOOLS,
    call_exposed_tool,
    exposed_tool_specs,
    handle_jsonrpc,
    read_stdio_message,
    resolve_mcp_base_dir,
    write_stdio_message,
)
from services.agent.skills.gallery import gallery_registry
from utils.runtime_session import write_latest_session_pointer


def _write_results(base: Path) -> None:
    rows = [
        {
            "file": "drum_01.jpg",
            "overall_score": 90.0,
            "scores": {"overall": 90.0},
            "category": "AI_Best_90+",
            "semantic_gate": {"status": "pass", "mode": "observe"},
            "tags": ["drummer"],
            "reason": "鼓手特写",
        }
    ]
    (base / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")


def _frame(obj: dict) -> bytes:
    buf = io.BytesIO()
    write_stdio_message(buf, obj)
    return buf.getvalue()


def _read_frames(raw: bytes) -> list[dict]:
    reader = io.BytesIO(raw)
    out: list[dict] = []
    while True:
        msg = read_stdio_message(reader)
        if msg is None:
            break
        out.append(msg)
    return out


def test_exposed_surface_excludes_job_and_db_tools(tmp_path: Path) -> None:
    _write_results(tmp_path)
    reg = gallery_registry(str(tmp_path))
    names = {spec["name"] for spec in exposed_tool_specs(reg)}
    assert names == set(MCP_EXPOSED_TOOLS)
    schema = next(spec for spec in exposed_tool_specs(reg) if spec["name"] == "gallery_search")
    assert "inputSchema" in schema
    assert "function" not in schema
    blocked = call_exposed_tool(reg, "submit_curation_job", {"user_text": "这场交10张给客户"})
    assert blocked["ok"] is False


def test_jsonrpc_list_and_search(tmp_path: Path) -> None:
    _write_results(tmp_path)
    reg = gallery_registry(str(tmp_path))
    listed = handle_jsonrpc(reg, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    names = {item["name"] for item in listed["result"]["tools"]}
    assert "gallery_search" in names
    called = handle_jsonrpc(
        reg,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "gallery_search", "arguments": {"query": "鼓手", "limit": 3}},
        },
    )
    assert called is not None
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert called["result"]["isError"] is False


def test_initialize_and_notification(tmp_path: Path) -> None:
    _write_results(tmp_path)
    reg = gallery_registry(str(tmp_path))
    init = handle_jsonrpc(
        reg,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test"}},
        },
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "livehouse-gallery"
    assert init["result"]["protocolVersion"] == "2024-11-05"
    assert handle_jsonrpc(reg, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    ping = handle_jsonrpc(reg, {"jsonrpc": "2.0", "id": 2, "method": "ping"})
    assert ping == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_resolve_mcp_base_dir_prefers_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LIVEHOUSE_MCP_BASE_DIR", str(tmp_path))
    assert resolve_mcp_base_dir() == str(tmp_path.resolve())


def test_resolve_mcp_base_dir_latest_session(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "Archive"
    previews = archive / "sess" / "Previews"
    previews.mkdir(parents=True)
    _write_results(previews)
    write_latest_session_pointer(previews)
    for key in (
        "LIVEHOUSE_MCP_BASE_DIR",
        "LIVEHOUSE_GALLERY_DIR",
        "LIVEHOUSE_GALLERY_PREVIEWS_DIR",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LUMA_ARCHIVE_ROOT", str(archive))
    assert resolve_mcp_base_dir(cwd=str(tmp_path)) == str(previews.resolve())


def test_stdio_smoke(tmp_path: Path) -> None:
    _write_results(tmp_path)
    req = _frame({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "services.agent.mcp_gallery"],
        input=req,
        capture_output=True,
        cwd=str(repo),
        env={**os.environ, "LIVEHOUSE_MCP_BASE_DIR": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    frames = _read_frames(proc.stdout)
    assert frames
    names = {item["name"] for item in frames[0]["result"]["tools"]}
    assert "gallery_search" in names
    assert "sqlite_query" not in names
