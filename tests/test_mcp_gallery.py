"""MCP adapter over gallery_registry — list/call + stdio smoke."""
from __future__ import annotations

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
)
from services.agent.skills.gallery import gallery_registry


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


def test_exposed_surface_excludes_job_and_db_tools(tmp_path: Path) -> None:
    _write_results(tmp_path)
    reg = gallery_registry(str(tmp_path))
    names = {spec["function"]["name"] for spec in exposed_tool_specs(reg)}
    assert names == set(MCP_EXPOSED_TOOLS)
    blocked = call_exposed_tool(reg, "submit_curation_job", {"user_text": "这场交10张给客户"})
    assert blocked["ok"] is False


def test_jsonrpc_list_and_search(tmp_path: Path) -> None:
    _write_results(tmp_path)
    reg = gallery_registry(str(tmp_path))
    listed = handle_jsonrpc(reg, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {item["function"]["name"] for item in listed["result"]["tools"]}
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
    payload = json.loads(called["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert called["result"]["isError"] is False


def test_stdio_smoke(tmp_path: Path) -> None:
    _write_results(tmp_path)
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-m", "services.agent.mcp_gallery"],
        input=req,
        text=True,
        capture_output=True,
        cwd=str(repo),
        env={**os.environ, "LIVEHOUSE_MCP_BASE_DIR": str(tmp_path)},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    response = json.loads(proc.stdout.strip().splitlines()[0])
    names = {item["function"]["name"] for item in response["result"]["tools"]}
    assert "gallery_search" in names
    assert "sqlite_query" not in names
