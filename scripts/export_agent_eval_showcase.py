#!/usr/bin/env python3
"""Export archived Agent evaluation data for the read-only Vercel dashboard."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api import eval_routes  # noqa: E402

DEFAULT_FIXTURES_DIR = ROOT / "web" / "fixtures"
_PATH_RES = [
    re.compile(r"/Volumes/[^\"'\s\\]+"),
    re.compile(r"/Users/[^\"'\s\\]+"),
]
_HOST_RE = re.compile(r"\b[\w-]+\.local\b")


def _scrub(value: Any) -> Any:
    """Remove machine-specific paths and hostnames before publishing."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        scrubbed = value
        for pattern in _PATH_RES:
            scrubbed = pattern.sub("/archive/redacted", scrubbed)
        return _HOST_RE.sub("host.local", scrubbed)
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def export_snapshots(
    archive_dir: Path,
    fixtures_dir: Path = DEFAULT_FIXTURES_DIR,
    *,
    history_limit: int = 365,
) -> tuple[Path, Path]:
    """Write compact latest and history fixtures using the production API schema."""
    env_name = "LIVEHOUSE_AGENT_EVAL_REPORTS_DIR"
    previous = os.environ.get(env_name)
    os.environ[env_name] = str(archive_dir.expanduser().resolve())
    try:
        latest = eval_routes.agent_eval_latest(include_cases=True)
        history = eval_routes.agent_eval_runs(limit=history_limit)
    finally:
        if previous is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = previous

    latest_path = fixtures_dir / "agent-eval-latest.json"
    history_path = fixtures_dir / "agent-eval-history.json"
    _write_json(latest_path, _scrub(latest))
    _write_json(history_path, _scrub(history))
    return latest_path, history_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive-dir",
        type=Path,
        required=True,
        help="Directory containing index.json and immutable Agent evaluation runs",
    )
    parser.add_argument("--fixtures-dir", type=Path, default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--history-limit", type=int, default=365)
    args = parser.parse_args()
    if not 1 <= args.history_limit <= 365:
        parser.error("--history-limit must be between 1 and 365")

    latest_path, history_path = export_snapshots(
        args.archive_dir,
        args.fixtures_dir,
        history_limit=args.history_limit,
    )
    print(f"exported {latest_path.relative_to(ROOT)}")
    print(f"exported {history_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
