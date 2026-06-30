#!/usr/bin/env python3
"""One-shot artifacts for resumes/interviews: benchmark JSON + reliability harness reports."""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Write reports/portfolio/benchmark_inference.json and reports/portfolio/reliability/ "
            "(reliability_report.json + .md). Uses the same defaults as docs/BENCHMARK.md quick run."
        )
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=str(_ROOT / "reports" / "portfolio"),
        help="Base directory for artifacts (default: reports/portfolio).",
    )
    parser.add_argument(
        "--benchmark-args",
        type=str,
        default="",
        help='Extra args forwarded to scripts/benchmark_inference.py (string), e.g. "--requests 60"',
    )
    args = parser.parse_args()

    base = Path(args.out_dir).resolve()
    bench_json = base / "benchmark_inference.json"
    rel_dir = base / "reliability"

    bench_cmd = [
        sys.executable,
        str(_ROOT / "scripts" / "benchmark_inference.py"),
        "--workers",
        "1,4",
        "--requests",
        "120",
        "--concurrency",
        "24",
        "--output-json",
        str(bench_json),
    ]
    if args.benchmark_args.strip():
        bench_cmd.extend(shlex.split(args.benchmark_args))

    chaos_cmd = [
        sys.executable,
        str(_ROOT / "scripts" / "chaos_runtime.py"),
        "--report-dir",
        str(rel_dir),
    ]

    subprocess.run(bench_cmd, cwd=str(_ROOT), check=True)
    r = subprocess.run(chaos_cmd, cwd=str(_ROOT), check=False)
    print(f"# portfolio_snapshot: wrote\n#   {bench_json}\n#   {rel_dir}/reliability_report.json\n#   {rel_dir}/reliability_report.md")
    return int(r.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
