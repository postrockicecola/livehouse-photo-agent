"""Runnable MCP server entrypoint: ``python -m services.agent.mcp``.

Serves Agent Skills over newline-delimited JSON-RPC on stdio — the shape an MCP host
(Claude Desktop, Cursor, another agent) launches.

Examples::

    # Built-in sandbox (+ optional read-only SQLite)
    python -m services.agent.mcp --db-path data/luma_brain.db

    # Gallery session skills (search / stats / explain) for a previews dir
    python -m services.agent.mcp --gallery-dir /path/to/Previews

Cursor ``mcp.json`` fragment::

    {
      "mcpServers": {
        "livehouse-gallery": {
          "command": "python",
          "args": ["-m", "services.agent.mcp", "--gallery-dir", "/path/to/Previews"]
        }
      }
    }
"""
from __future__ import annotations

import argparse
import sys

from services.agent.mcp.server import McpServer, serve_stdio
from services.agent.skills import default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="services.agent.mcp", description="MCP server over stdio for agent skills.")
    parser.add_argument("--db-path", default=None, help="Optional SQLite path to enable the read-only query skill.")
    parser.add_argument(
        "--gallery-dir",
        default=None,
        help="Session Previews dir — expose gallery_search / gallery_stats / explain_photo as MCP tools.",
    )
    parser.add_argument("--name", default="livehouse-agent-skills", help="Server name advertised on initialize.")
    args = parser.parse_args(argv)

    if args.gallery_dir:
        from services.agent.skills.gallery import gallery_registry

        registry = gallery_registry(args.gallery_dir)
        if args.name == "livehouse-agent-skills":
            args.name = "livehouse-gallery"
    else:
        registry = default_registry(db_path=args.db_path)
    server = McpServer(registry, name=args.name)
    serve_stdio(server, sys.stdin, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())