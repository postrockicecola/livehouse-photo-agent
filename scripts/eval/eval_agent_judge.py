#!/usr/bin/env python3
"""Phase-6 weak LLM-judge / human-rubric gate for Gallery Copilot turns.

Always applies groundedness hard rules after scoring. Failures can be written as
review-queue stubs for ``promote_to_fixtures``.

Run::

    # CI: scripted judge over mock agent turns
    python -m scripts.eval.eval_agent_judge --mock --suite smoke

    # Nightly: real chat model as judge (and real agent if --live-agent)
    python -m scripts.eval.eval_agent_judge --live --suite smoke

    # Score a human-filled ratings jsonl (still groundedness-gated)
    python -m scripts.eval.eval_agent_judge --human data/eval/agent/ratings/example.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality.agent_cases import load_jsonl  # noqa: E402
from scripts.eval.eval_agent_live import evaluate as eval_live  # noqa: E402
from services.agent.quality_judge import (  # noqa: E402
    DEFAULT_PASS_MIN,
    human_rating,
    judge_turn,
    rating_to_review_stub,
)

ChatFn = Callable[[list[dict[str, str]]], str]


def _scripted_generous_judge(_messages: list[dict[str, str]]) -> str:
    return json.dumps(
        {"useful": 4, "honest": 5, "concise": 4, "rationale": "scripted mock judge"},
        ensure_ascii=False,
    )


def _build_live_judge(config_path: str) -> ChatFn:
    from services.agent.chat_backend import build_chat_fn
    from utils.config_loader import ConfigLoader

    model_cfg = ConfigLoader.get_model_config(ConfigLoader.load(config_path))
    chat_model = str(model_cfg.get("agent_chat_model") or model_cfg.get("model_name") or "").strip() or None
    return build_chat_fn(model_cfg, model_name=chat_model)


def judge_agent_report(
    agent_report: dict[str, Any],
    *,
    judge_fn: ChatFn,
    pass_min: int = DEFAULT_PASS_MIN,
    rater: str = "llm_judge",
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for case in agent_report.get("cases") or []:
        if not isinstance(case, dict):
            continue
        # Reconstruct a minimal tool_calls list for groundedness.
        tools = case.get("tools") or []
        files = case.get("files") or []
        tool_calls = [
            {
                "tool": t,
                "ok": True,
                "metadata": {"files": files} if i == 0 else {},
            }
            for i, t in enumerate(tools)
        ]
        utterance = str(case.get("utterance") or case.get("id") or "")
        reply = str(case.get("reply") or "")
        verdict = judge_turn(
            judge_fn,
            utterance=utterance,
            reply=reply,
            tool_calls=tool_calls,
            working_memory={"last_files": files},
            pass_min=pass_min,
            rater=rater,
        )
        rows.append(
            verdict.to_rating_row(
                case_id=str(case.get("id") or "unknown"),
                utterance=utterance,
                reply=reply,
                tool_calls=tool_calls,
            )
        )

    passed = sum(1 for r in rows if r.get("pass"))
    gated = sum(1 for r in rows if r.get("gated"))
    return {
        "total": len(rows),
        "passed": passed,
        "pass_rate": round(passed / len(rows), 4) if rows else 0.0,
        "gated_count": gated,
        "mean_useful": round(
            sum(r["scores"]["useful"] for r in rows) / len(rows), 3
        )
        if rows
        else 0.0,
        "mean_honest": round(
            sum(r["scores"]["honest"] for r in rows) / len(rows), 3
        )
        if rows
        else 0.0,
        "mean_concise": round(
            sum(r["scores"]["concise"] for r in rows) / len(rows), 3
        )
        if rows
        else 0.0,
        "ratings": rows,
    }


def judge_with_utterances(
    agent_report: dict[str, Any],
    cases_by_id: dict[str, dict[str, Any]],
    *,
    judge_fn: ChatFn,
    pass_min: int = DEFAULT_PASS_MIN,
    rater: str = "llm_judge",
) -> dict[str, Any]:
    # Patch utterances onto cases before scoring
    patched = dict(agent_report)
    patched_cases = []
    for case in agent_report.get("cases") or []:
        c = dict(case)
        src = cases_by_id.get(str(c.get("id") or ""))
        if src:
            c["utterance"] = src.get("utterance")
        patched_cases.append(c)
    patched["cases"] = patched_cases
    return judge_agent_report(patched, judge_fn=judge_fn, pass_min=pass_min, rater=rater)


def score_human_jsonl(path: Path, *, pass_min: int = DEFAULT_PASS_MIN) -> dict[str, Any]:
    rows_in = load_jsonl(path)
    ratings: list[dict[str, Any]] = []
    for row in rows_in:
        scores = row.get("scores") if isinstance(row.get("scores"), dict) else row
        verdict = human_rating(
            scores,
            reply=str(row.get("reply") or ""),
            tool_calls=list(row.get("tool_calls") or []),
            working_memory=dict(row.get("working_memory") or {}),
            pass_min=pass_min,
            rationale=str(row.get("rationale") or row.get("notes") or ""),
        )
        ratings.append(
            verdict.to_rating_row(
                case_id=str(row.get("case_id") or row.get("id") or "human"),
                utterance=str(row.get("utterance") or ""),
                reply=str(row.get("reply") or ""),
                tool_calls=list(row.get("tool_calls") or []),
            )
        )
    passed = sum(1 for r in ratings if r.get("pass"))
    return {
        "total": len(ratings),
        "passed": passed,
        "pass_rate": round(passed / len(ratings), 4) if ratings else 0.0,
        "gated_count": sum(1 for r in ratings if r.get("gated")),
        "ratings": ratings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="Scripted judge + mock agent suite")
    mode.add_argument("--live", action="store_true", help="Real model as judge")
    mode.add_argument("--human", type=str, default="", help="Score human ratings jsonl")
    parser.add_argument("--suite", default="smoke", choices=("smoke", "core", "all"))
    parser.add_argument("--config", default="configs/livehouse.yaml")
    parser.add_argument("--pass-min", type=int, default=DEFAULT_PASS_MIN)
    parser.add_argument("--live-agent", action="store_true", help="With --live, also run live agent")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=str, default="", help="Write full JSON report")
    parser.add_argument(
        "--promote-stubs",
        type=str,
        default="",
        help="Write failing ratings as review-queue stubs jsonl",
    )
    args = parser.parse_args(argv)

    if args.human:
        report = score_human_jsonl(Path(args.human), pass_min=int(args.pass_min))
        report["mode"] = "human"
    else:
        from scripts.eval.eval_agent_live import select_live_cases

        agent_mode = "live" if (args.live and args.live_agent) else "mock"
        agent_report = eval_live(suite=args.suite, mode=agent_mode, config_path=args.config)
        cases = {c["id"]: c for c in select_live_cases(suite=args.suite)}
        if args.mock:
            judge_fn: ChatFn = _scripted_generous_judge
            rater = "llm_judge_mock"
        else:
            judge_fn = _build_live_judge(args.config)
            rater = "llm_judge"
        report = judge_with_utterances(
            agent_report,
            cases,
            judge_fn=judge_fn,
            pass_min=int(args.pass_min),
            rater=rater,
        )
        report["mode"] = "mock" if args.mock else "live"
        report["agent_mode"] = agent_mode
        report["suite"] = args.suite

    fails = [r for r in report.get("ratings") or [] if not r.get("pass")]
    if args.promote_stubs:
        stubs = [rating_to_review_stub(r) for r in fails]
        outp = Path(args.promote_stubs)
        outp.parent.mkdir(parents=True, exist_ok=True)
        with outp.open("w", encoding="utf-8") as f:
            for s in stubs:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        report["promote_stubs_path"] = str(outp)
        report["promote_stubs"] = len(stubs)

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"agent_judge[{report.get('mode')}]: "
            f"{report['passed']}/{report['total']} pass_rate={report['pass_rate']} "
            f"gated={report.get('gated_count', 0)} "
            f"useful={report.get('mean_useful', '-')} "
            f"honest={report.get('mean_honest', '-')} "
            f"concise={report.get('mean_concise', '-')}"
        )
        for r in fails[:20]:
            print(
                f"  [FAIL] {r.get('case_id')} grounded={r.get('grounded_ok')} "
                f"scores={r.get('scores')} :: {r.get('rationale')}"
            )

    # Mock judge suite is a CI gate; live/human are informational unless all fail parse.
    if args.mock:
        return 0 if report["passed"] == report["total"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
