#!/usr/bin/env python3
"""Start the local livehouse-gallery MCP server (repo-root aware)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.agent.mcp_gallery import main

if __name__ == "__main__":
    raise SystemExit(main())
