#!/usr/bin/env python3
"""L0 badcase / eval harness for the Gallery conversational agent.

Loads ``data/eval/agent/cases.v1.jsonl`` (see ``data/eval/agent/CONTRACT.txt``).
No live LLM: a scripted ``chat_fn`` emits planned tool calls, then a final answer.
Routed utterances exercise ``intent_router`` + one prose completion.

Run::

    python -m scripts.eval.eval_agent_chat_cases
    python -m scripts.eval.eval_agent_chat_cases --split smoke
    python -m scripts.eval.eval_agent_chat_cases --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from quality.agent_cases import load_agent_cases, session_dir  # noqa: E402
from services.agent.conversation import (  # noqa: E402
    ConversationalAgent,
    ConversationMemory,
    _parse_tool_call,
)
from services.agent.groundedness import extract_file_mentions, normalize_file_key  # noqa: E402
from services.agent.skills.artifacts import WriteArtifactSkill  # noqa: E402
from services.agent.skills.gallery import gallery_registry  # noqa: E402
from services.agent.skills.memory import register_memory_skills  # noqa: E402


def _scripted_chat(queue: list[str]) -> Callable[[list[dict[str, str]]], str]:
    q = list(queue)

    def _fn(_messages: list[dict[str, str]]) -> str:
        if q:
            return q.pop(0)
        return "Done."

    return _fn


def _collect_files(tool_calls: list[dict[str, Any]]) -> list[str]:
    files: list[str] = []
    for tc in tool_calls:
        meta = tc.get("metadata") or {}
        for key in ("files", "selected_keys"):
            vals = meta.get(key) or []
            if isinstance(vals, list):
                files.extend(str(f) for f in vals)
    # preserve order, unique
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _route_id(agent: ConversationalAgent) -> Optional[str]:
    trace = getattr(agent, "last_trace", None) or {}
    if isinstance(trace, dict) and trace.get("rule_id"):
        return str(trace["rule_id"])
    backend = str(getattr(agent, "last_backend", "") or "")
    if backend.startswith("routed:"):
        return backend.split(":", 1)[1]
    return None


def run_case(case: dict[str, Any], base_dir: Path, prefs: dict[str, str]) -> dict[str, Any]:
    t0 = time.monotonic()
    os.environ["LIVEHOUSE_CURATION_JOB_BACKEND"] = "defer"
    os.environ["LIVEHOUSE_AGENT_DB"] = str(base_dir / "agent_store.db")
    reg = gallery_registry(str(base_dir))
    artifact_dir = base_dir / "_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    reg.register(
        WriteArtifactSkill(str(artifact_dir), url_prefix="/eval/artifacts")
    )

    def _persist(k: str, v: str) -> None:
        prefs[k] = v

    register_memory_skills(reg, owner="eval", persist=_persist, loader=lambda: dict(prefs))
    mem = ConversationMemory(system_prompt="eval", max_tokens=4000)
    queue = list(case.get("model_queue") or [])
    agent = ConversationalAgent(
        _scripted_chat(queue),
        memory=mem,
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=3,
    )
    result = agent.chat(str(case["utterance"]))
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    tools = [str(tc.get("tool") or "") for tc in result.tool_calls]
    files = _collect_files(result.tool_calls)
    exp = case.get("expect") or {}
    ok = True
    reasons: list[str] = []

    if "route" in exp:
        got_route = _route_id(agent)
        want_route = exp.get("route")
        if want_route != got_route:
            ok = False
            reasons.append(f"route {got_route!r} != {want_route!r}")

    want_tools = list(exp.get("tools") or [])
    if want_tools:
        if exp.get("tools_ordered"):
            # subsequence match in order
            ti = 0
            for w in want_tools:
                while ti < len(tools) and tools[ti] != w:
                    ti += 1
                if ti >= len(tools):
                    ok = False
                    reasons.append(f"tools_ordered missing {w} in {tools}")
                    break
                ti += 1
        else:
            missing = [t for t in want_tools if t not in tools]
            if missing:
                ok = False
                reasons.append(f"missing tools {missing}")
    elif exp.get("tools") == [] and tools:
        ok = False
        reasons.append(f"expected no tools, got {tools}")

    if exp.get("select_after") and "gallery_select" not in tools:
        ok = False
        reasons.append("expected gallery_select after search")

    if "min_files" in exp and len(files) < int(exp["min_files"]):
        ok = False
        reasons.append(f"files {len(files)} < {exp['min_files']}")

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

    if exp.get("reply_must_not_json", True) and _parse_tool_call(result.reply or "") is not None:
        ok = False
        reasons.append("reply still looks like tool JSON")

    for needle in exp.get("reply_must_not_contain") or []:
        if str(needle) and str(needle) in (result.reply or ""):
            ok = False
            reasons.append(f"reply contains forbidden {needle!r}")

    # Default-on for tool turns: every cited image basename must be in tool/WM files.
    check_grounded = exp.get("grounded")
    if check_grounded is None:
        check_grounded = bool(result.tool_calls)
    if check_grounded:
        allowed = {normalize_file_key(f) for f in files}
        for f in result.working_memory.get("last_files") or []:
            allowed.add(normalize_file_key(str(f)))
        cited = extract_file_mentions(result.reply or "")
        bad = [c for c in cited if c not in allowed]
        if bad:
            ok = False
            reasons.append(f"ungrounded cites {bad}")

    return {
        "id": case["id"],
        "split": case.get("split"),
        "ok": ok,
        "reasons": reasons,
        "tool_calls": len(result.tool_calls),
        "tools": tools,
        "route": _route_id(agent),
        "files": files[:12],
        "elapsed_ms": elapsed_ms,
        "reply": (result.reply or "")[:200],
        "backend": getattr(agent, "last_backend", ""),
        "grounding_events": sum(1 for e in result.events if e.get("type") == "grounding_violation"),
    }


def evaluate(
    cases: list[dict[str, Any]] | None = None,
    *,
    splits: Optional[list[str]] = None,
) -> dict[str, Any]:
    cases = cases if cases is not None else load_agent_cases(splits=splits)
    prefs: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Materialize each distinct session fixture once under tmp (skills may write).
        session_roots: dict[str, Path] = {}
        for case in cases:
            sess = str(case.get("session") or "smoke")
            if sess not in session_roots:
                src = session_dir(sess)
                dst = tmp_path / sess
                shutil.copytree(src, dst)
                session_roots[sess] = dst
            rows.append(run_case(case, session_roots[sess], prefs))
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
    parser.add_argument(
        "--split",
        action="append",
        dest="splits",
        help="Filter split (repeatable). Default: all.",
    )
    args = parser.parse_args(argv)
    report = evaluate(splits=args.splits)
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
            route = c.get("route")
            route_s = f" route={route}" if route else ""
            print(
                f"  [{mark}] {c['id']} tools={c['tool_calls']}{route_s} "
                f"files={c['files']}{extra}"
            )
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
