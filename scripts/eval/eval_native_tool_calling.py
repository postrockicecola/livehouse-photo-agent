#!/usr/bin/env python3
"""A/B: text-protocol tool JSON vs Ollama native tools=[] (same model).

Measures first-decide parse failure rate and latency for prompts that *should*
emit a tool call. Does not replace the production path — only exercises
``build_chat_fn`` with ``native_tools`` on/off.

Run (requires local Ollama + agent_chat_model, default qwen2.5:3b-instruct):

    python -m scripts.eval.eval_native_tool_calling
    python -m scripts.eval.eval_native_tool_calling --repeats 2 --json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.agent.chat_backend import build_chat_fn, content_from_assistant_message
from services.agent.conversation import _parse_tool_call
from services.agent.skills.gallery import gallery_registry
from services.agent.skills.memory import register_memory_skills
from utils.config_loader import ConfigLoader

# Minimal catalog — mirrors production AVAILABLE TOOLS shape (names + required).
_PROTOCOL = (
    "You are the Gallery copilot. To use a tool, reply with ONLY a single JSON object:\n"
    '{"tool": "<tool_name>", "args": { ... }}\n'
    "When finished, answer in plain natural language (no JSON)."
)

# Prompts that should trigger a tool on the first model call (not chitchat).
CASES: list[dict[str, Any]] = [
    {"id": "search_drummer", "user": "找鼓手特写", "expect_tools": {"gallery_search"}},
    {"id": "best_photos", "user": "帮我留下最好的照片", "expect_tools": {"gallery_search", "gallery_select"}},
    {"id": "drop_low_quality", "user": "筛掉技术不行的、糊的", "expect_tools": {"gallery_search"}},
    {"id": "stats", "user": "这场 session 概况怎么样", "expect_tools": {"gallery_stats"}},
    {"id": "energy_sort", "user": "按 energy 给我前 10 张", "expect_tools": {"gallery_search"}},
    {"id": "remember", "user": "记住以后少选剪影", "expect_tools": {"remember_preference"}},
]


@dataclass
class Trial:
    case_id: str
    mode: str
    ok_parse: bool
    tool: Optional[str]
    latency_ms: float
    raw_preview: str
    error: Optional[str] = None


@dataclass
class ModeSummary:
    mode: str
    n: int
    parse_fail_rate: float
    parse_ok: int
    avg_latency_ms: float
    p50_latency_ms: float
    trials: list[Trial] = field(default_factory=list)


def _tool_catalog(specs: list[dict[str, Any]]) -> str:
    slim = [
        {
            "name": s["function"]["name"],
            "description": s["function"]["description"],
            "required": s["function"]["parameters"].get("required", []),
        }
        for s in specs
    ]
    return json.dumps(slim, ensure_ascii=False)


def _system_prompt(specs: list[dict[str, Any]]) -> str:
    return f"{_PROTOCOL}\n\nAVAILABLE TOOLS:\n{_tool_catalog(specs)}"


def _build_registry(base_dir: str):
    reg = gallery_registry(base_dir)
    prefs: dict[str, str] = {}
    register_memory_skills(
        reg,
        owner="eval-native-tools",
        persist=lambda k, v: prefs.__setitem__(k, v),
        loader=lambda: dict(prefs),
    )
    return reg


def _run_mode(
    *,
    mode: str,
    model_cfg: dict[str, Any],
    model_name: str,
    specs: list[dict[str, Any]],
    system: str,
    repeats: int,
    temperature: float,
) -> ModeSummary:
    native = mode == "native"
    chat = build_chat_fn(
        model_cfg,
        model_name=model_name,
        tools=specs if native else None,
        native_tools=native,
        temperature=temperature,
        num_predict=256,
        timeout=90,
    )
    trials: list[Trial] = []
    for case in CASES:
        for _ in range(max(1, repeats)):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": case["user"]},
            ]
            t0 = time.perf_counter()
            err: Optional[str] = None
            raw = ""
            try:
                raw = chat(messages)
            except Exception as exc:  # transport / HTTP
                err = str(exc)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            parsed = _parse_tool_call(raw) if raw else None
            # Native path already bridges tool_calls → text JSON via ChatFn; also
            # accept empty content as fail.
            tool = parsed["tool"] if parsed else None
            expect = case.get("expect_tools") or set()
            ok = bool(parsed) and (not expect or tool in expect)
            # Count "parse failure" as: expected a tool but none parseable (wrong
            # tool name still counts as parse-ok for format metrics).
            ok_parse = parsed is not None
            trials.append(
                Trial(
                    case_id=case["id"],
                    mode=mode,
                    ok_parse=ok_parse,
                    tool=tool,
                    latency_ms=latency_ms,
                    raw_preview=(raw or "")[:180].replace("\n", " "),
                    error=err,
                )
            )
            _ = ok  # reserved for stricter tool-name accuracy in --verbose dumps

    n = len(trials)
    fails = sum(1 for t in trials if not t.ok_parse or t.error)
    lats = [t.latency_ms for t in trials if not t.error]
    return ModeSummary(
        mode=mode,
        n=n,
        parse_fail_rate=(fails / n) if n else 1.0,
        parse_ok=n - fails,
        avg_latency_ms=statistics.mean(lats) if lats else 0.0,
        p50_latency_ms=statistics.median(lats) if lats else 0.0,
        trials=trials,
    )


def evaluate(
    *,
    base_dir: Optional[str] = None,
    model_name: Optional[str] = None,
    repeats: int = 1,
    temperature: float = 0.2,
) -> dict[str, Any]:
    cfg = ConfigLoader.load()
    model_cfg = ConfigLoader.get_model_config(cfg)
    chat_model = (
        model_name
        or str(model_cfg.get("agent_chat_model") or "").strip()
        or str(model_cfg.get("model_name") or "").strip()
    )
    root = base_dir or str(ROOT / "tests" / "_tmp_native_tools_eval")
    Path(root).mkdir(parents=True, exist_ok=True)
    if not (Path(root) / "analysis_results.json").exists():
        (Path(root) / "analysis_results.json").write_text("[]", encoding="utf-8")

    reg = _build_registry(root)
    specs = reg.tool_specs()
    system = _system_prompt(specs)

    text = _run_mode(
        mode="text",
        model_cfg=model_cfg,
        model_name=chat_model,
        specs=specs,
        system=system,
        repeats=repeats,
        temperature=temperature,
    )
    native = _run_mode(
        mode="native",
        model_cfg=model_cfg,
        model_name=chat_model,
        specs=specs,
        system=system,
        repeats=repeats,
        temperature=temperature,
    )

    # Conclusion heuristic for the report.
    delta = text.parse_fail_rate - native.parse_fail_rate
    if abs(delta) < 0.05:
        conclusion = (
            "No meaningful difference in parse-fail rate (±5pp). Native tools do not "
            "clearly beat the text protocol on this model/prompt set."
        )
    elif delta > 0:
        conclusion = (
            f"Native tools lower parse-fail rate by ~{delta:.0%} absolute. "
            "Worth considering as an opt-in for decide-step reliability."
        )
    else:
        conclusion = (
            f"Text protocol had lower parse-fail rate (native worse by ~{-delta:.0%}). "
            "Keep production on text JSON; native path not a win here."
        )

    return {
        "model": chat_model,
        "provider": model_cfg.get("provider"),
        "repeats": repeats,
        "temperature": temperature,
        "n_cases": len(CASES),
        "text": {**asdict(text), "trials": [asdict(t) for t in text.trials]},
        "native": {**asdict(native), "trials": [asdict(t) for t in native.trials]},
        "conclusion": conclusion,
        # Smoke that bridge helpers stay importable for offline unit use.
        "bridge_smoke": bool(content_from_assistant_message({"content": "x"})),
    }


def _print_human(report: dict[str, Any]) -> None:
    print(f"model={report['model']}  provider={report['provider']}  repeats={report['repeats']}")
    print()
    for key in ("text", "native"):
        m = report[key]
        print(
            f"[{key}] n={m['n']}  parse_fail_rate={m['parse_fail_rate']:.1%}  "
            f"parse_ok={m['parse_ok']}  avg_ms={m['avg_latency_ms']:.0f}  "
            f"p50_ms={m['p50_latency_ms']:.0f}"
        )
        for t in m["trials"]:
            mark = "ok" if t["ok_parse"] and not t["error"] else "FAIL"
            tool = t["tool"] or "-"
            err = f" err={t['error'][:60]}" if t["error"] else ""
            print(
                f"  {mark:4}  {t['case_id']:18}  tool={tool:22}  "
                f"{t['latency_ms']:.0f}ms  {t['raw_preview'][:70]}{err}"
            )
        print()
    print("conclusion:", report["conclusion"])


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model", default=None, help="Override agent_chat_model")
    p.add_argument("--base-dir", default=None, help="Gallery previews dir (for tool schema only)")
    p.add_argument("--repeats", type=int, default=1, help="Repeats per case (reduces noise)")
    p.add_argument("--temperature", type=float, default=0.2)
    p.add_argument("--json", action="store_true", help="Print full JSON report")
    args = p.parse_args(argv)

    report = evaluate(
        base_dir=args.base_dir,
        model_name=args.model,
        repeats=args.repeats,
        temperature=args.temperature,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
