#!/usr/bin/env python3
"""Evaluate cross-session archive retrieval with Recall@K, MRR, NDCG, and MAP.

Case JSONL rows:
  {"id":"drummers","query":"鼓手","expected_files":["session__a.jpg"],"k":5}

Run:
  python -m scripts.eval.eval_archive_search_retrieval \
    --archive-root /path/to/Livehouse_Archive --cases cases.jsonl --json
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_eval.metrics import _ranking
from services.agent.skills.archive_search import ArchiveSearchSkill
from utils.studio_sessions import scan_archive_session_dirs


def _load_cases(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            rows.append(row)
    return rows


def evaluate(
    *,
    archive_root: Path,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    sessions = scan_archive_session_dirs(archive_root)
    base_dir = next(
        (str(row.get("previews_dir")) for row in sessions if row.get("previews_dir")),
        str(archive_root),
    )
    previous_root = os.environ.get("LUMA_ARCHIVE_ROOT")
    os.environ["LUMA_ARCHIVE_ROOT"] = str(archive_root.resolve())
    try:
        skill = ArchiveSearchSkill(base_dir)
        results: list[dict[str, Any]] = []
        for case in cases:
            args = dict(case.get("args") or {})
            args.setdefault("query", str(case.get("query") or ""))
            k = max(1, int(case.get("k") or args.get("limit") or 10))
            args.setdefault("limit", k)
            outcome = skill.run(args)
            actual = list((outcome.metadata or {}).get("files") or [])
            expected = [str(x) for x in (case.get("expected_files") or [])]
            ranking = _ranking(
                expected,
                actual,
                k=k,
                relevance=case.get("relevance"),
            )
            allowed_sessions = {
                str(x) for x in (case.get("allowed_sessions") or []) if str(x)
            }
            filter_ok = not allowed_sessions or all(
                any(file_id.startswith(f"{session}__") for session in allowed_sessions)
                for file_id in actual
            )
            results.append(
                {
                    "id": str(case.get("id") or f"case_{len(results)}"),
                    "ok": bool(outcome.ok) and filter_ok,
                    "expected": expected,
                    "actual": actual,
                    "filter_ok": filter_ok,
                    **ranking,
                }
            )
    finally:
        if previous_root is None:
            os.environ.pop("LUMA_ARCHIVE_ROOT", None)
        else:
            os.environ["LUMA_ARCHIVE_ROOT"] = previous_root

    def _mean(key: str) -> float:
        values = [float(row[key]) for row in results if row.get(key) is not None]
        return round(sum(values) / len(values), 4) if values else 0.0

    return {
        "schema_version": "archive_search_retrieval_eval.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "archive_root": str(archive_root.resolve()),
        "total": len(results),
        "passed": sum(1 for row in results if row["ok"]),
        "metrics": {
            "recall_at_k": _mean("recall_at_k"),
            "precision_at_k": _mean("precision_at_k"),
            "mrr": _mean("reciprocal_rank"),
            "ndcg": _mean("ndcg"),
            "map": _mean("average_precision"),
        },
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = evaluate(
        archive_root=args.archive_root,
        cases=_load_cases(args.cases),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    if args.json or not args.out:
        print(rendered)
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
