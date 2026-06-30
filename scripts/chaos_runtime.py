#!/usr/bin/env python3
"""Run the full reliability / chaos scenario matrix (structured output for demos)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Repo root on ``python scripts/chaos_runtime.py``
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from reliability_harness import write_reports_to_dir
from reliability_scenarios import print_report, run_all_scenarios, run_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Systematic runtime reliability checks (SSOT jobs, dispatch, inference)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON array (CI / dashboards); default is human-readable report.",
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        default="",
        help=(
            "If set, write unified reliability_report.json + reliability_report.md under this "
            "directory (structured harness output). Stdout behavior unchanged unless --json."
        ),
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="Comma-separated scenario ids (default: all). See docs/RELIABILITY.md.",
    )
    args = parser.parse_args()

    if args.only.strip():
        only = frozenset(x.strip() for x in args.only.split(",") if x.strip())
        results = run_scenarios(only_ids=only)
    else:
        results = run_all_scenarios()

    report_dir = (args.report_dir or "").strip()
    if report_dir:
        paths = write_reports_to_dir(report_dir, results)
        if not args.json:
            print(
                f"# Wrote harness reports:\n#   JSON: {paths['json']}\n#   Markdown: {paths['markdown']}\n",
                file=sys.stderr,
            )

    print_report(results, as_json=args.json)
    if not all(r.ok for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
