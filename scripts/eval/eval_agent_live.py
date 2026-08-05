#!/usr/bin/env python3
"""L1 live / mock Gallery Copilot eval (pass@1 + trajectory metrics).

``--mock`` (CI): scripted ``model_queue`` ChatFn — exercises the live scorer.
``--live`` (nightly): real chat backend from ``configs/livehouse.yaml``.
``--live --strict``: also enforce ``_LIVE_STRICT_THRESHOLDS`` as a release gate
(deliberately relaxed until a few weeks of real-model baselines land).

Run::

    python -m scripts.eval.eval_agent_live --mock --suite smoke
    python -m scripts.eval.eval_agent_live --live --suite smoke --json
    python -m scripts.eval.eval_agent_live --live --suite smoke --strict
    python -m scripts.eval.eval_agent_live --mock --suite core --out /tmp/agent_live.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
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
from scripts.eval.agent_case_score import aggregate_scores, score_case  # noqa: E402
from services.agent.conversation import ConversationalAgent, ConversationMemory  # noqa: E402
from services.agent.skills.artifacts import WriteArtifactSkill  # noqa: E402
from services.agent.skills.gallery import gallery_registry  # noqa: E402
from services.agent.skills.memory import register_memory_skills  # noqa: E402

ChatFn = Callable[[list[dict[str, str]]], str]

# Initial real-model release gate (Phase 3.5). Deliberately looser than the
# frozen mock thresholds below — raise these once a few weeks of --live
# baselines confirm the real chat model holds the line. Only enforced with
# --strict; plain --live stays informational.
_LIVE_STRICT_THRESHOLDS = {
    "pass_at_1": 0.85,
    "grounded_rate": 0.95,
    "json_leak_rate": 0.05,
}


def _scripted_chat(queue: list[str]) -> ChatFn:
    q = list(queue)

    def _fn(_messages: list[dict[str, str]]) -> str:
        if q:
            return q.pop(0)
        return "Done."

    return _fn


def _tool_catalog(registry) -> str:
    tools = [
        {
            "name": s["function"]["name"],
            "description": s["function"]["description"],
            "args": s["function"]["parameters"].get("properties", {}),
            "required": s["function"]["parameters"].get("required", []),
        }
        for s in registry.tool_specs()
    ]
    return json.dumps(tools, ensure_ascii=False)


def build_eval_system_prompt(registry) -> str:
    """Prefer production prompt constants; fall back to a slim protocol."""
    try:
        from api.agent_routes import PROTOCOL_PROMPT, SEMANTIC_HINTS, STYLE_PROMPT

        return (
            f"{PROTOCOL_PROMPT}\n\n{STYLE_PROMPT}\n\n{SEMANTIC_HINTS}\n\n"
            f"AVAILABLE TOOLS:\n{_tool_catalog(registry)}"
        )
    except Exception:
        return (
            "You are the Gallery copilot. To use a tool, reply with ONLY JSON:\n"
            '{"tool": "<name>", "args": { ... }}\n'
            "When finished, answer in plain language (no JSON).\n\n"
            f"AVAILABLE TOOLS:\n{_tool_catalog(registry)}"
        )


def prompt_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


# Extra stable cases pulled into the smoke suite even when split=core.
_SMOKE_EXTRA_IDS = frozenset(
    {
        "routed_social_wechat",
        "routed_energy",
        "routed_dedupe",
        "routed_quality",
        "gallery_stats",
        "plain_help_no_tools",
    }
)


def select_live_cases(
    *,
    suite: str,
    cases: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Filter cases marked ``live: true`` into smoke / core suites."""
    rows = cases if cases is not None else load_agent_cases()
    live_rows = [c for c in rows if c.get("live")]
    suite = (suite or "smoke").strip().lower()
    if suite == "smoke":
        return [
            c
            for c in live_rows
            if c.get("split") == "smoke" or c.get("id") in _SMOKE_EXTRA_IDS
        ]
    if suite == "core":
        return [c for c in live_rows if c.get("split") in ("smoke", "core")]
    if suite == "all":
        return live_rows
    raise ValueError(f"unknown suite {suite!r}; use smoke|core|all")


def _build_live_chat_fn(
    *,
    config_path: str,
    native_tools: bool,
    registry,
) -> tuple[ChatFn, dict[str, Any]]:
    from services.agent.chat_backend import build_chat_fn
    from utils.config_loader import ConfigLoader

    cfg = ConfigLoader.load(config_path)
    model_cfg = ConfigLoader.get_model_config(cfg)
    chat_model = (
        str(model_cfg.get("agent_chat_model") or model_cfg.get("model_name") or "").strip()
        or str(model_cfg.get("model_name") or "llava")
    )
    tools = registry.tool_specs() if native_tools else None
    chat_fn = build_chat_fn(
        model_cfg,
        model_name=chat_model,
        tools=tools,
        native_tools=native_tools,
    )
    meta = {
        "provider": model_cfg.get("provider"),
        "model_name": chat_model,
        "native_tools": bool(native_tools),
    }
    return chat_fn, meta


def run_live_case(
    case: dict[str, Any],
    *,
    base_dir: Path,
    chat_fn: ChatFn,
    system_prompt: str,
    prefs: dict[str, str],
    live: bool,
) -> dict[str, Any]:
    reg = gallery_registry(str(base_dir))
    artifact_dir = base_dir / "_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    reg.register(WriteArtifactSkill(str(artifact_dir), url_prefix="/eval/artifacts"))

    def _persist(k: str, v: str) -> None:
        prefs[k] = v

    register_memory_skills(reg, owner="eval-live", persist=_persist, loader=lambda: dict(prefs))
    mem = ConversationMemory(system_prompt=system_prompt, max_tokens=4000)
    agent = ConversationalAgent(
        chat_fn,
        memory=mem,
        skills=reg,
        wrap_tool_output=False,
        max_tool_rounds=3,
    )
    t0 = time.monotonic()
    result = agent.chat(str(case["utterance"]))
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return score_case(
        case=case,
        reply=result.reply,
        tool_calls=list(result.tool_calls),
        working_memory=dict(result.working_memory),
        prefs=prefs,
        backend=str(getattr(agent, "last_backend", "") or ""),
        elapsed_ms=elapsed_ms,
        events=list(result.events),
        live=live,
    )


def evaluate(
    *,
    suite: str = "smoke",
    mode: str = "mock",
    config_path: str = "configs/livehouse.yaml",
    native_tools: bool = False,
    cases: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    mode = mode.strip().lower()
    if mode not in ("mock", "live"):
        raise ValueError("mode must be mock|live")

    selected = select_live_cases(suite=suite, cases=cases)
    if not selected:
        raise RuntimeError(
            f"no live cases for suite={suite!r}; mark cases with \"live\": true in cases.v1.jsonl"
        )

    prefs: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    model_meta: dict[str, Any] = {"mode": mode, "native_tools": native_tools}
    sys_prompt = ""
    p_hash = ""

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        session_roots: dict[str, Path] = {}

        # Build registry once on first session for prompt / live chat_fn.
        first_sess = str(selected[0].get("session") or "smoke")
        src0 = session_dir(first_sess)
        dst0 = tmp_path / first_sess
        shutil.copytree(src0, dst0)
        session_roots[first_sess] = dst0
        bootstrap_reg = gallery_registry(str(dst0))
        register_memory_skills(
            bootstrap_reg,
            owner="eval-live",
            persist=lambda k, v: prefs.__setitem__(k, v),
            loader=lambda: dict(prefs),
        )
        sys_prompt = build_eval_system_prompt(bootstrap_reg)
        p_hash = prompt_hash(sys_prompt)

        if mode == "live":
            chat_fn, model_meta = _build_live_chat_fn(
                config_path=config_path,
                native_tools=native_tools,
                registry=bootstrap_reg,
            )
            model_meta = {**model_meta, "mode": "live"}
        else:
            model_meta = {
                "mode": "mock",
                "model_name": "scripted",
                "provider": "mock",
                "native_tools": False,
            }

        for case in selected:
            sess = str(case.get("session") or "smoke")
            if sess not in session_roots:
                shutil.copytree(session_dir(sess), tmp_path / sess)
                session_roots[sess] = tmp_path / sess
            if mode == "mock":
                fn: ChatFn = _scripted_chat(list(case.get("model_queue") or []))
            else:
                fn = chat_fn
            rows.append(
                run_live_case(
                    case,
                    base_dir=session_roots[sess],
                    chat_fn=fn,
                    system_prompt=sys_prompt,
                    prefs=prefs,
                    live=(mode == "live"),
                )
            )

    metrics = aggregate_scores(rows)
    return {
        "suite": suite,
        "mode": mode,
        "prompt_hash": p_hash,
        "model": model_meta,
        "metrics": metrics,
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--mock", action="store_true", help="Scripted ChatFn (CI)")
    group.add_argument("--live", action="store_true", help="Real model from config")
    parser.add_argument("--suite", default="smoke", choices=("smoke", "core", "all"))
    parser.add_argument("--config", default="configs/livehouse.yaml")
    parser.add_argument("--native-tools", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=str, default="", help="Write full JSON report")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="With --live, enforce _LIVE_STRICT_THRESHOLDS instead of staying informational",
    )
    args = parser.parse_args(argv)

    mode = "live" if args.live else "mock"
    try:
        report = evaluate(
            suite=args.suite,
            mode=mode,
            config_path=args.config,
            native_tools=bool(args.native_tools),
        )
    except Exception as exc:
        print(f"eval_agent_live failed: {exc}", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    m = report["metrics"]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            f"agent_live[{report['mode']}/{report['suite']}]: "
            f"{m['passed']}/{m['total']} pass@1={m['pass_at_1']} "
            f"tool_acc={m['tool_name_acc']} route_acc={m['route_acc']} "
            f"grounded={m['grounded_rate']} json_leak={m['json_leak_rate']} "
            f"p50_ms={m['p50_latency_ms']} prompt_hash={report['prompt_hash']}"
        )
        for c in report["cases"]:
            mark = "PASS" if c["ok"] else "FAIL"
            extra = f" ({', '.join(c['reasons'])})" if c["reasons"] else ""
            print(f"  [{mark}] {c['id']} tools={c['tools']}{extra}")

    # Frozen mock thresholds (release gate). Live stays informational unless --strict.
    if mode == "mock":
        ok = (
            m["passed"] == m["total"]
            and float(m.get("pass_at_1") or 0) >= 1.0
            and float(m.get("json_leak_rate") or 0) <= 0.0
            and float(m.get("grounded_rate") or 0) >= 1.0
        )
        if not ok:
            print(
                "mock thresholds failed: "
                f"pass_at_1={m.get('pass_at_1')} json_leak={m.get('json_leak_rate')} "
                f"grounded={m.get('grounded_rate')}",
                file=sys.stderr,
            )
        return 0 if ok else 1

    if args.strict:
        thr = _LIVE_STRICT_THRESHOLDS
        ok = (
            float(m.get("pass_at_1") or 0) >= thr["pass_at_1"]
            and float(m.get("grounded_rate") or 0) >= thr["grounded_rate"]
            and float(m.get("json_leak_rate") or 1.0) <= thr["json_leak_rate"]
        )
        if not ok:
            print(
                "live --strict thresholds failed: "
                f"pass_at_1={m.get('pass_at_1')} (need >= {thr['pass_at_1']}) "
                f"grounded_rate={m.get('grounded_rate')} (need >= {thr['grounded_rate']}) "
                f"json_leak_rate={m.get('json_leak_rate')} (need <= {thr['json_leak_rate']})",
                file=sys.stderr,
            )
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
