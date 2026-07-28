#!/usr/bin/env python3
"""Promote annotated review-queue rows into Tier-1 router regression fixtures.

Stage ③ of the production→regression loop.

Reads jsonl rows with ``action=add_regression_test`` and appends unique cases to
``tests/agent/fixtures/router_cases.jsonl`` (never deletes; set ``deprecated``
manually if a case retires).

Usage::

    python -m scripts.agent.promote_to_fixtures data/review_queue/2026-07-28.jsonl
    python -m scripts.agent.promote_to_fixtures data/review_queue/2026-07-28.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_ROUTER = ROOT / "tests" / "agent" / "fixtures" / "router_cases.jsonl"
_ISSUE_TYPES = {
    "missed_route",
    "wrong_route",
    "negation_missed",
    "guardrail_false_positive",
    "guardrail_missed",
    "hallucination",
    "style_violation",
    "other",
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def row_to_router_case(row: dict[str, Any], *, added: str) -> dict[str, Any] | None:
    if str(row.get("action") or "").strip() != "add_regression_test":
        return None
    user = str(row.get("user_text") or "").strip()
    if not user:
        return None
    exp = row.get("expected_behavior") if isinstance(row.get("expected_behavior"), dict) else {}
    should_route = exp.get("should_route")
    rule_id = exp.get("rule_id")
    if should_route is False:
        rule_id = None
    expected_args = exp.get("expected_args") if isinstance(exp.get("expected_args"), dict) else {}
    case: dict[str, Any] = {
        "input": user,
        "expected_rule_id": rule_id,
        "source": f"conv_{row.get('conversation_id')}",
        "added": added,
        "issue_type": str(row.get("issue_type") or "other"),
        "deprecated": False,
    }
    if expected_args:
        case["expected_args"] = expected_args
    if row.get("notes"):
        case["notes"] = str(row.get("notes"))[:500]
    return case


def promote(
    review_path: Path,
    *,
    router_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    incoming = _load_jsonl(review_path)
    existing = _load_jsonl(router_path)
    by_input = {
        str(r.get("input") or "").strip(): i
        for i, r in enumerate(existing)
        if str(r.get("input") or "").strip()
    }

    added: list[str] = []
    skipped: list[str] = []
    ignored = 0
    for row in incoming:
        action = str(row.get("action") or "").strip()
        if action in ("ignore", "not_a_bug", "needs_prompt_fix", ""):
            if action:
                ignored += 1
            continue
        case = row_to_router_case(row, added=str(row.get("reviewed_at") or today))
        if case is None:
            continue
        key = case["input"]
        if key in by_input:
            skipped.append(key)
            continue
        existing.append(case)
        by_input[key] = len(existing) - 1
        added.append(key)

    if not dry_run and added:
        _write_jsonl(router_path, existing)

    return {
        "review": str(review_path),
        "router_fixture": str(router_path),
        "added": len(added),
        "skipped_dup": len(skipped),
        "ignored_or_deferred": ignored,
        "added_inputs": added,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_jsonl", type=str, help="Annotated review queue jsonl")
    parser.add_argument(
        "--router-out",
        type=str,
        default=str(_DEFAULT_ROUTER),
        help="Tier-1 fixture path",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    review_path = Path(args.review_jsonl)
    if not review_path.is_file():
        print(f"error: not found: {review_path}", file=sys.stderr)
        return 2

    report = promote(review_path, router_path=Path(args.router_out), dry_run=bool(args.dry_run))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
