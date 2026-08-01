#!/usr/bin/env python3
"""Intent-router paraphrase precision / recall (no LLM).

Reads ``data/eval/agent/router_paraphrases.v1.jsonl``.

Run::

    python -m scripts.eval.eval_agent_router_paraphrases
    python -m scripts.eval.eval_agent_router_paraphrases --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality.agent_cases import load_router_paraphrases  # noqa: E402
from services.agent.intent_router import route_gallery_intent  # noqa: E402


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return {
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "f1": round(f1, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def evaluate(rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = rows if rows is not None else load_router_paraphrases()
    # Per expected rule_id (skip null-only families for P/R on that id)
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    case_rows: list[dict[str, Any]] = []
    n_ok = 0

    for row in rows:
        utt = str(row["utterance"])
        want: Optional[str] = row.get("expect_rule")
        match = route_gallery_intent(utt)
        got = match.rule_id if match is not None else None
        ok = got == want
        if ok:
            n_ok += 1
        reasons: list[str] = []
        if not ok:
            reasons.append(f"got {got!r} want {want!r}")

        if match is not None and "expect_select_after" in row:
            if bool(match.select_after_search) != bool(row["expect_select_after"]):
                ok = False
                reasons.append(
                    f"select_after {match.select_after_search} != {row['expect_select_after']}"
                )
        if match is not None and "expect_limit" in row:
            lim = match.calls[0].args.get("limit") if match.calls else None
            if lim != row["expect_limit"]:
                ok = False
                reasons.append(f"limit {lim!r} != {row['expect_limit']!r}")

        # Confusion vs each non-null gold label
        if want is not None:
            if got == want:
                stats[want]["tp"] += 1
            else:
                stats[want]["fn"] += 1
                if got is not None:
                    stats[got]["fp"] += 1
        else:
            if got is not None:
                stats[got]["fp"] += 1

        case_rows.append(
            {
                "id": row["id"],
                "ok": ok,
                "utterance": utt,
                "expect_rule": want,
                "got_rule": got,
                "rule_family": row.get("rule_family"),
                "polarity": row.get("polarity"),
                "reasons": reasons,
            }
        )

    by_rule = {rule: _prf(v["tp"], v["fp"], v["fn"]) for rule, v in sorted(stats.items())}
    # Micro over all non-null expectations
    tp = sum(v["tp"] for v in stats.values())
    fp = sum(v["fp"] for v in stats.values())
    fn = sum(v["fn"] for v in stats.values())
    return {
        "total": len(case_rows),
        "passed": n_ok,
        "success_rate": round(n_ok / len(case_rows), 4) if case_rows else 0.0,
        "micro": _prf(tp, fp, fn),
        "by_rule": by_rule,
        "cases": case_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = evaluate()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        m = report["micro"]
        print(
            f"agent_router_paraphrases: {report['passed']}/{report['total']} "
            f"success_rate={report['success_rate']} "
            f"micro_p={m['precision']} r={m['recall']} f1={m['f1']}"
        )
        for rule, s in report["by_rule"].items():
            print(
                f"  {rule}: P={s['precision']} R={s['recall']} F1={s['f1']} "
                f"(tp={s['tp']} fp={s['fp']} fn={s['fn']})"
            )
        for c in report["cases"]:
            if c["ok"]:
                continue
            print(f"  [FAIL] {c['id']}: {', '.join(c['reasons'])} :: {c['utterance']!r}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
