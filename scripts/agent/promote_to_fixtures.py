#!/usr/bin/env python3
"""Promote annotated review-queue rows into regression fixtures.

Stage ③ of the production→regression loop.

Reads jsonl rows with ``action=add_regression_test`` and appends unique cases to:

- ``tests/agent/fixtures/router_cases.jsonl`` (router Tier-1)
- ``data/eval/agent/router_paraphrases.v1.jsonl`` (L0 paraphrase table)
- ``data/eval/agent/cases.v1.jsonl`` (chat L0 stubs for hallucination / parse issues)

Usage::

    python -m scripts.agent.promote_to_fixtures data/review_queue/2026-07-28.jsonl
    python -m scripts.agent.promote_to_fixtures data/review_queue/2026-07-28.jsonl --dry-run
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_DEFAULT_ROUTER = ROOT / "tests" / "agent" / "fixtures" / "router_cases.jsonl"
_DEFAULT_PARAPHRASES = ROOT / "data" / "eval" / "agent" / "router_paraphrases.v1.jsonl"
_DEFAULT_AGENT_CASES = ROOT / "data" / "eval" / "agent" / "cases.v1.jsonl"
_AGENT_ISSUE_TYPES = frozenset({"hallucination", "style_violation", "other"})
_ROUTE_ISSUE_TYPES = frozenset({"missed_route", "wrong_route", "negation_missed"})
_SAFE_ID = re.compile(r"[^a-z0-9_]+")


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
    issue = str(row.get("issue_type") or "other")
    exp = row.get("expected_behavior") if isinstance(row.get("expected_behavior"), dict) else {}
    # Route fixtures: explicit route issues, or any row that sets should_route/rule_id.
    if issue not in _ROUTE_ISSUE_TYPES and exp.get("should_route") is None and not exp.get("rule_id"):
        if issue in _AGENT_ISSUE_TYPES:
            return None
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
        "issue_type": issue,
        "deprecated": False,
    }
    if expected_args:
        case["expected_args"] = expected_args
    if row.get("notes"):
        case["notes"] = str(row.get("notes"))[:500]
    return case


def row_to_paraphrase(row: dict[str, Any], *, idx: int) -> dict[str, Any] | None:
    """Mirror a router promotion into the L0 paraphrase table."""
    case = row_to_router_case(row, added="promoted")
    if case is None:
        return None
    rule = case.get("expected_rule_id")
    family = str(rule or "fallthrough")
    return {
        "schema_version": "agent_router_paraphrase.v1",
        "id": f"promoted_{row.get('conversation_id')}_{idx}",
        "utterance": case["input"],
        "expect_rule": rule,
        "rule_family": family,
        "polarity": "negative" if rule is None else "positive",
    }


def row_to_agent_case(row: dict[str, Any], *, added: str) -> dict[str, Any] | None:
    """Chat L0 stub for groundedness / parse / style regressions (needs model_queue fill)."""
    if str(row.get("action") or "").strip() != "add_regression_test":
        return None
    issue = str(row.get("issue_type") or "")
    reasons = {str(r) for r in (row.get("reasons") or [])}
    exp = row.get("expected_behavior") if isinstance(row.get("expected_behavior"), dict) else {}
    want = (
        issue in _AGENT_ISSUE_TYPES
        or bool(exp.get("promote_agent_case"))
        or bool(reasons & {"grounding_violation", "parse_fail"})
    )
    if not want:
        return None
    user = str(row.get("user_text") or "").strip()
    if not user:
        return None
    slug = _SAFE_ID.sub("_", user.lower())[:40].strip("_") or "utterance"
    cid = row.get("conversation_id") or "x"
    case_id = f"promoted_{cid}_{slug}"
    rule = exp.get("rule_id")
    if exp.get("should_route") is False:
        rule = None
    expect: dict[str, Any] = {
        "route": rule,
        "grounded": True,
        "reply_must_not_json": True,
    }
    if exp.get("tools"):
        expect["tools"] = list(exp["tools"])
    return {
        "schema_version": "agent_chat_case.v1",
        "id": case_id,
        "utterance": user,
        "session": "smoke",
        "model_queue": [
            # Human should replace with the failing scripted trajectory when promoting.
            "请根据工具结果简要回答，不要编造文件名。"
        ],
        "expect": expect,
        "tags": ["regression", "control"],
        "split": "regression",
        "live": False,
        "notes": (str(row.get("notes") or "") or f"promoted {added} issue={issue}")[:500],
    }


def _append_unique(
    existing: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
    *,
    key_fn,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    by_key = {key_fn(r): i for i, r in enumerate(existing)}
    added: list[str] = []
    skipped: list[str] = []
    for row in new_rows:
        key = key_fn(row)
        if not key:
            continue
        if key in by_key:
            skipped.append(key)
            continue
        existing.append(row)
        by_key[key] = len(existing) - 1
        added.append(key)
    return existing, added, skipped


def promote(
    review_path: Path,
    *,
    router_path: Path,
    paraphrase_path: Path,
    agent_cases_path: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    incoming = _load_jsonl(review_path)

    router_rows = _load_jsonl(router_path)
    para_rows = _load_jsonl(paraphrase_path)
    agent_rows = _load_jsonl(agent_cases_path)

    router_new: list[dict[str, Any]] = []
    para_new: list[dict[str, Any]] = []
    agent_new: list[dict[str, Any]] = []
    ignored = 0
    for i, row in enumerate(incoming):
        action = str(row.get("action") or "").strip()
        if action in ("ignore", "not_a_bug", "needs_prompt_fix", ""):
            if action:
                ignored += 1
            continue
        added = str(row.get("reviewed_at") or today)
        rc = row_to_router_case(row, added=added)
        if rc is not None:
            router_new.append(rc)
            pc = row_to_paraphrase(row, idx=i)
            if pc is not None:
                para_new.append(pc)
        ac = row_to_agent_case(row, added=added)
        if ac is not None:
            agent_new.append(ac)

    router_rows, router_added, router_skip = _append_unique(
        router_rows, router_new, key_fn=lambda r: str(r.get("input") or "").strip()
    )
    para_rows, para_added, para_skip = _append_unique(
        para_rows, para_new, key_fn=lambda r: str(r.get("utterance") or "").strip()
    )
    agent_rows, agent_added, agent_skip = _append_unique(
        agent_rows, agent_new, key_fn=lambda r: str(r.get("id") or "").strip()
    )

    if not dry_run:
        if router_added:
            _write_jsonl(router_path, router_rows)
        if para_added:
            _write_jsonl(paraphrase_path, para_rows)
        if agent_added:
            _write_jsonl(agent_cases_path, agent_rows)

    return {
        "review": str(review_path),
        "router_fixture": str(router_path),
        "paraphrases": str(paraphrase_path),
        "agent_cases": str(agent_cases_path),
        "router_added": len(router_added),
        "paraphrase_added": len(para_added),
        "agent_case_added": len(agent_added),
        "skipped_dup": {
            "router": len(router_skip),
            "paraphrase": len(para_skip),
            "agent_case": len(agent_skip),
        },
        "ignored_or_deferred": ignored,
        "added_inputs": router_added,
        "added_agent_ids": agent_added,
        "dry_run": dry_run,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review_jsonl", type=str, help="Annotated review queue jsonl")
    parser.add_argument("--router-out", type=str, default=str(_DEFAULT_ROUTER))
    parser.add_argument("--paraphrase-out", type=str, default=str(_DEFAULT_PARAPHRASES))
    parser.add_argument("--agent-cases-out", type=str, default=str(_DEFAULT_AGENT_CASES))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    review_path = Path(args.review_jsonl)
    if not review_path.is_file():
        print(f"error: not found: {review_path}", file=sys.stderr)
        return 2

    report = promote(
        review_path,
        router_path=Path(args.router_out),
        paraphrase_path=Path(args.paraphrase_out),
        agent_cases_path=Path(args.agent_cases_out),
        dry_run=bool(args.dry_run),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
