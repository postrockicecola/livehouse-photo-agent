"""
Unified reliability verification harness: structured report documents for demos, CI, and interviews.

Consumers: ``scripts/chaos_runtime.py`` (``--report-dir``). Scenario implementations live in
``reliability_scenarios.py``; this module wraps JSON/Markdown emission only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reliability_scenarios import ChaosScenarioResult, results_to_jsonable

SCHEMA_VERSION = "1"


def build_report_document(results: list[ChaosScenarioResult]) -> dict[str, Any]:
    """Single JSON-serializable payload (summary + scenarios + assertions + metrics)."""
    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now,
        "title": "Livehouse reliability harness",
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "all_passed": failed == 0,
        },
        "scenarios": results_to_jsonable(results),
    }


def write_json_report(path: Path | str, results: list[ChaosScenarioResult]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_report_document(results)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_markdown_report(path: Path | str, results: list[ChaosScenarioResult]) -> Path:
    """Human-readable summary table + per-scenario detail (good for README screenshots / interviews)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_report_document(results)
    summary = doc["summary"]
    lines: list[str] = [
        "# Reliability harness report",
        "",
        f"- Generated (UTC): `{doc['generated_at']}`",
        f"- Schema: `{doc['schema_version']}`",
        f"- Result: **{summary['passed']} passed**, **{summary['failed']} failed** (total {summary['total']})",
        "",
        "## Summary table",
        "",
        "| Scenario | Status | Key assertions | Metrics (abbrev.) |",
        "|----------|--------|----------------|-------------------|",
    ]
    for row in doc["scenarios"]:
        sid = str(row.get("id", ""))
        ok = bool(row.get("ok"))
        status = "PASS" if ok else "FAIL"
        assertions = row.get("assertions") or []
        ast = "; ".join(str(a) for a in assertions[:2])
        if len(assertions) > 2:
            ast += "…"
        metrics = row.get("metrics") or {}
        met = json.dumps(metrics, ensure_ascii=False) if metrics else "—"
        if len(met) > 80:
            met = met[:77] + "…"
        lines.append(f"| `{sid}` | {status} | {ast} | `{met}` |")
    lines.extend(
        [
            "",
            "## Per-scenario detail",
            "",
        ]
    )
    for row in doc["scenarios"]:
        sid = str(row.get("id", ""))
        ok = bool(row.get("ok"))
        status = "PASS" if ok else "FAIL"
        lines.append(f"### [{status}] `{sid}`")
        lines.append("")
        if row.get("design"):
            lines.append(f"- **Design**: {row['design']}")
        if row.get("interview_line"):
            lines.append(f"- **Interview line**: {row['interview_line']}")
        assertions = row.get("assertions") or []
        if assertions:
            lines.append("- **Assertions:**")
            for a in assertions:
                lines.append(f"  - {a}")
        metrics = row.get("metrics") or {}
        if metrics:
            lines.append(f"- **Metrics:** `{json.dumps(metrics, ensure_ascii=False)}`")
        ev = row.get("evidence")
        if ev:
            lines.append(f"- **Evidence:** `{json.dumps(ev, ensure_ascii=False)}`")
        lines.append("")
    lines.extend(
        [
            "## Reproducing",
            "",
            "Run from repo root:",
            "",
            "```bash",
            "python scripts/chaos_runtime.py",
            "python scripts/chaos_runtime.py --report-dir reports/reliability",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_reports_to_dir(report_dir: Path | str, results: list[ChaosScenarioResult]) -> dict[str, Path]:
    """Write `reliability_report.json` and `reliability_report.md` under ``report_dir``."""
    base = Path(report_dir)
    return {
        "json": write_json_report(base / "reliability_report.json", results),
        "markdown": write_markdown_report(base / "reliability_report.md", results),
    }
