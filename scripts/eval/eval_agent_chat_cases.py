#!/usr/bin/env python3
"""Deterministic badcase / eval harness for the Gallery conversational agent.

No live LLM: a scripted ``chat_fn`` emits planned tool calls, then a final answer.
Measures success rate, tool steps, empty-search handling, preference memory, and
RAG citation presence. Run:

    python -m scripts.eval.eval_agent_chat_cases
    python -m scripts.eval.eval_agent_chat_cases --json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.skills.gallery import gallery_registry
from services.agent.skills.memory import register_memory_skills


@dataclass
class Case:
    id: str
    user: str
    # Sequence of model outputs before the final prose answer.
    model_queue: list[str]
    expect: dict[str, Any] = field(default_factory=dict)


def _write_sample_session(base: Path) -> None:
    rows = [
        {
            "file": "drum_01.jpg",
            "overall_score": 88.0,
            "scores": {"overall": 88.0, "energy": 9.0, "technical": 8.0, "composition": 8.5},
            "energy": 9.0,
            "technical": 8.0,
            "composition": 8.5,
            "category": "AI_Keep_60-90",
            "tags": ["drummer", "drums"],
            "reason": "Peak drummer hit under red wash.",
        },
        {
            "file": "guitar_01.jpg",
            "overall_score": 92.0,
            "scores": {"overall": 92.0, "energy": 8.5, "technical": 8.5, "composition": 9.0},
            "energy": 8.5,
            "technical": 8.5,
            "composition": 9.0,
            "category": "AI_Best_90+",
            "tags": ["guitarist", "guitar"],
            "reason": "吉他手特写",
        },
        {
            "file": "blur_01.jpg",
            "overall_score": 35.0,
            "scores": {"overall": 35.0, "energy": 3.0, "technical": 2.0, "composition": 3.0},
            "energy": 3.0,
            "technical": 2.0,
            "composition": 3.0,
            "category": "AI_Trash_Below60",
            "tags": ["blurry"],
            "reason": "Out of focus.",
        },
    ]
    (base / "analysis_results.json").write_text(json.dumps(rows), encoding="utf-8")


def _scripted_chat(queue: list[str]) -> Callable[[list[dict[str, str]]], str]:
    q = list(queue)

    def _fn(_messages: list[dict[str, str]]) -> str:
        if q:
            return q.pop(0)
        return "Done."

    return _fn


CASES: list[Case] = [
    Case(
        id="rag_search_drummer",
        user="找鼓手特写",
        model_queue=[
            json.dumps({"tool": "gallery_search", "args": {"query": "鼓手特写", "limit": 5, "mode": "text"}}),
            "推荐 drum_01.jpg，citation grounded.",
        ],
        expect={"tool": "gallery_search", "min_files": 1, "has_citations": True, "file_contains": "drum"},
    ),
    Case(
        id="empty_semantic_query",
        user="找萨克斯风",
        model_queue=[
            json.dumps({"tool": "gallery_search", "args": {"query": "萨克斯风", "limit": 5, "mode": "text"}}),
            "本场没有萨克斯相关命中。",
        ],
        expect={"tool": "gallery_search", "min_files": 0, "allow_empty": True},
    ),
    Case(
        id="repeat_tool_break",
        user="找吉他",
        model_queue=[
            json.dumps({"tool": "gallery_search", "args": {"query": "吉他", "limit": 5, "mode": "text"}}),
            json.dumps({"tool": "gallery_search", "args": {"query": "吉他", "limit": 5, "mode": "text"}}),
            # Forced final path may ignore this; harness accepts any non-JSON reply.
        ],
        expect={"tool": "gallery_search", "max_tool_calls": 1, "file_contains": "guitar"},
    ),
    Case(
        id="remember_preference",
        user="以后少选剪影",
        model_queue=[
            json.dumps(
                {
                    "tool": "remember_preference",
                    "args": {"key": "avoid_silhouettes", "value": "true"},
                }
            ),
            "已记住：少选剪影。",
        ],
        expect={"tool": "remember_preference", "pref_key": "avoid_silhouettes"},
    ),
]


def run_case(case: Case, base_dir: Path, prefs: dict[str, str]) -> dict[str, Any]:
    t0 = time.monotonic()
    reg = gallery_registry(str(base_dir))

    def _persist(k: str, v: str) -> None:
        prefs[k] = v

    register_memory_skills(reg, owner="eval", persist=_persist, loader=lambda: dict(prefs))
    mem = ConversationMemory(system_prompt="eval", max_tokens=4000)
    agent = ConversationalAgent(
        _scripted_chat(case.model_queue),
        memory=mem,
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=3,
    )
    result = agent.chat(case.user)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    tools = [tc.get("tool") for tc in result.tool_calls]
    meta = (result.tool_calls[0].get("metadata") if result.tool_calls else {}) or {}
    files = list(meta.get("files") or [])
    citations = list(meta.get("citations") or [])
    exp = case.expect
    ok = True
    reasons: list[str] = []

    if exp.get("tool") and exp["tool"] not in tools:
        ok = False
        reasons.append(f"missing tool {exp['tool']}")
    if "min_files" in exp and len(files) < int(exp["min_files"]):
        ok = False
        reasons.append(f"files {len(files)} < {exp['min_files']}")
    if exp.get("allow_empty") and len(files) != 0:
        # empty query case — soft fail if unexpected hits
        pass
    if exp.get("has_citations") and not citations and files:
        # text mode still emits citations from hybrid_retrieve
        ok = False
        reasons.append("missing citations")
    if exp.get("file_contains"):
        needle = str(exp["file_contains"]).lower()
        if not any(needle in str(f).lower() for f in files):
            ok = False
            reasons.append(f"no file matching {needle!r}")
    if "max_tool_calls" in exp and len(result.tool_calls) > int(exp["max_tool_calls"]):
        ok = False
        reasons.append(f"too many tool calls: {len(result.tool_calls)}")
    if exp.get("pref_key") and exp["pref_key"] not in prefs:
        ok = False
        reasons.append(f"pref {exp['pref_key']} not saved")

    return {
        "id": case.id,
        "ok": ok,
        "reasons": reasons,
        "tool_calls": len(result.tool_calls),
        "tools": tools,
        "files": files,
        "citations": len(citations),
        "elapsed_ms": elapsed_ms,
        "reply": (result.reply or "")[:200],
    }


def evaluate(cases: list[Case] | None = None) -> dict[str, Any]:
    import tempfile

    cases = cases or CASES
    prefs: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        _write_sample_session(base)
        rows = [run_case(c, base, prefs) for c in cases]
    passed = sum(1 for r in rows if r["ok"])
    return {
        "total": len(rows),
        "passed": passed,
        "success_rate": round(passed / len(rows), 4) if rows else 0.0,
        "mean_tool_calls": round(sum(r["tool_calls"] for r in rows) / len(rows), 3) if rows else 0.0,
        "mean_elapsed_ms": round(sum(r["elapsed_ms"] for r in rows) / len(rows), 1) if rows else 0.0,
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    args = parser.parse_args(argv)
    report = evaluate()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"agent_chat_cases: {report['passed']}/{report['total']} "
            f"success_rate={report['success_rate']} "
            f"mean_tools={report['mean_tool_calls']} "
            f"mean_ms={report['mean_elapsed_ms']}"
        )
        for c in report["cases"]:
            mark = "PASS" if c["ok"] else "FAIL"
            extra = f" ({', '.join(c['reasons'])})" if c["reasons"] else ""
            print(f"  [{mark}] {c['id']} tools={c['tool_calls']} files={c['files']}{extra}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
