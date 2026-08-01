#!/usr/bin/env python3
"""Summarize Gallery agent quality for a weekly review.

Merges:
  - latest L1 mock/live eval JSON reports (optional ``--eval`` paths)
  - review-queue jsonl (optional ``--review``)
  - quick L0 gate status (always runs in-process)

Usage::

    python -m scripts.eval.report_agent_quality_weekly
    python -m scripts.eval.report_agent_quality_weekly \\
        --eval /tmp/agent_live_smoke.json \\
        --review data/review_queue/2026-08-01.jsonl \\
        --out data/eval/agent/reports/weekly.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


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


def _run_l0() -> dict[str, Any]:
    from scripts.eval import eval_agent_judge as judge_mod
    from scripts.eval.eval_agent_chat_cases import evaluate as eval_chat
    from scripts.eval.eval_agent_live import evaluate as eval_live, select_live_cases
    from scripts.eval.eval_agent_router_paraphrases import evaluate as eval_router

    chat = eval_chat()
    router = eval_router()
    live_smoke = eval_live(suite="smoke", mode="mock")
    cases = {c["id"]: c for c in select_live_cases(suite="smoke")}
    judge_report = judge_mod.judge_with_utterances(
        live_smoke,
        cases,
        judge_fn=judge_mod._scripted_generous_judge,
        rater="llm_judge_mock",
    )
    return {
        "chat_cases": {
            "passed": chat["passed"],
            "total": chat["total"],
            "success_rate": chat["success_rate"],
        },
        "router_paraphrases": {
            "passed": router["passed"],
            "total": router["total"],
            "micro_f1": router["micro"]["f1"],
        },
        "live_mock_smoke": live_smoke["metrics"],
        "judge_mock_smoke": {
            "passed": judge_report["passed"],
            "total": judge_report["total"],
            "pass_rate": judge_report["pass_rate"],
            "gated_count": judge_report["gated_count"],
        },
    }


def render_markdown(
    *,
    l0: dict[str, Any],
    eval_reports: list[dict[str, Any]],
    review_rows: list[dict[str, Any]],
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Agent quality weekly — {now}",
        "",
        "## L0 gates (in-process)",
        "",
        f"- chat_cases: {l0['chat_cases']['passed']}/{l0['chat_cases']['total']} "
        f"(rate={l0['chat_cases']['success_rate']})",
        f"- router_paraphrases: {l0['router_paraphrases']['passed']}/{l0['router_paraphrases']['total']} "
        f"(micro_f1={l0['router_paraphrases']['micro_f1']})",
        f"- live_mock_smoke pass@1: {l0['live_mock_smoke'].get('pass_at_1')} "
        f"({l0['live_mock_smoke'].get('passed')}/{l0['live_mock_smoke'].get('total')})",
        f"- judge_mock_smoke: {l0['judge_mock_smoke']['passed']}/{l0['judge_mock_smoke']['total']} "
        f"(pass_rate={l0['judge_mock_smoke']['pass_rate']}, gated={l0['judge_mock_smoke']['gated_count']})",
        "",
    ]
    if eval_reports:
        lines.append("## External eval reports")
        lines.append("")
        for rep in eval_reports:
            m = rep.get("metrics") or {}
            lines.append(
                f"- mode={rep.get('mode')} suite={rep.get('suite')} "
                f"pass@1={m.get('pass_at_1')} tool_acc={m.get('tool_name_acc')} "
                f"route_acc={m.get('route_acc')} grounded={m.get('grounded_rate')} "
                f"json_leak={m.get('json_leak_rate')} prompt_hash={rep.get('prompt_hash')}"
            )
            fails = [c for c in (rep.get("cases") or []) if not c.get("ok")]
            for c in fails[:12]:
                lines.append(
                    f"  - FAIL `{c.get('id')}`: {', '.join(c.get('reasons') or [])}"
                )
        lines.append("")

    lines.append("## Review queue")
    lines.append("")
    if not review_rows:
        lines.append("- (no review jsonl provided or empty)")
    else:
        reason_counts: Counter[str] = Counter()
        issue_counts: Counter[str] = Counter()
        action_counts: Counter[str] = Counter()
        for r in review_rows:
            for reason in r.get("reasons") or []:
                reason_counts[str(reason)] += 1
            issue_counts[str(r.get("issue_type") or "(unannotated)")] += 1
            action_counts[str(r.get("action") or "(unannotated)")] += 1
        lines.append(f"- candidates: {len(review_rows)}")
        lines.append(
            "- signals: "
            + ", ".join(f"{k}={v}" for k, v in sorted(reason_counts.items()))
        )
        lines.append(
            "- issue_type: "
            + ", ".join(f"{k}={v}" for k, v in sorted(issue_counts.items()))
        )
        lines.append(
            "- action: "
            + ", ".join(f"{k}={v}" for k, v in sorted(action_counts.items()))
        )
        pending = [
            r
            for r in review_rows
            if not str(r.get("action") or "").strip()
            or str(r.get("action")) == "add_regression_test"
            and not str(r.get("reviewed_at") or "").strip()
        ]
        lines.append(f"- still needing human fill: ~{len(pending)}")
        lines.append("")
        lines.append("### Next actions")
        lines.append("")
        lines.append("1. Annotate `issue_type` / `expected_behavior` / `action` in the review jsonl")
        lines.append("2. `python -m scripts.agent.promote_to_fixtures <review.jsonl>`")
        lines.append("3. Re-run `python -m scripts.eval.eval_agent_chat_cases` + router paraphrases")

    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval", action="append", default=[], help="Eval JSON report path (repeatable)")
    parser.add_argument("--review", type=str, default="", help="Review queue jsonl")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Write markdown (default stdout; suggested data/eval/agent/reports/weekly.md)",
    )
    parser.add_argument("--json", action="store_true", help="Also print machine JSON summary")
    args = parser.parse_args(argv)

    l0 = _run_l0()
    eval_reports = []
    for p in args.eval:
        rep = _load_json(Path(p))
        if rep:
            eval_reports.append(rep)
    review_rows = _load_jsonl(Path(args.review)) if args.review else []

    md = render_markdown(l0=l0, eval_reports=eval_reports, review_rows=review_rows)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"wrote {out}")
    else:
        print(md, end="")

    if args.json:
        print(
            json.dumps(
                {"l0": l0, "eval_reports": len(eval_reports), "review_candidates": len(review_rows)},
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
