"""Runnable MCP server entrypoint: ``python -m services.agent.mcp``.

Serves the built-in Agent Skills (code execution + optional read-only SQLite) over
newline-delimited JSON-RPC on stdio — the shape an MCP host (Claude Desktop, an IDE,
another agent) launches. Point a host's MCP config at this command to drive the
project's skills as MCP tools.

    python -m services.agent.mcp [--db-path PATH] [--name NAME]
"""
from __future__ import annotations

import argparse
import sys

from services.agent.mcp.server import McpServer, serve_stdio
from services.agent.skills import default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.agent.mcp", description="MCP server over stdio for agent skills.")
    parser.add_argument("--db-path", default=None, help="Optional SQLite path to enable the read-only query skill.")
    parser.add_argument("--name", default="livehouse-agent-skills", help="Server name advertised on initialize.")
    args = parser.parse_args(argv)

    registry = default_registry(db_path=args.db_path)
    server = McpServer(registry, name=args.name)
    serve_stdio(server, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
