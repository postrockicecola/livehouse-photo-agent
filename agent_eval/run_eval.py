#!/usr/bin/env python3
"""Run the real Gallery LangGraph agent against production-style benchmarks."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_eval import SCHEMA_VERSION  # noqa: E402
from agent_eval.case_loader import load_cases, load_golden_cases  # noqa: E402
from agent_eval.decision_trace import build_decision_trace  # noqa: E402
from agent_eval.html_report import render_report  # noqa: E402
from agent_eval.instrumentation import (  # noqa: E402
    InstrumentedChat,
    InstrumentedRegistry,
    memory_delta,
)
from agent_eval.metrics import aggregate, composite_score, evaluate_case  # noqa: E402
from agent_eval.regression import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    compare,
    threshold_failures,
)
from quality.agent_cases import session_dir  # noqa: E402
from scripts.eval.eval_agent_live import build_eval_system_prompt  # noqa: E402
from services.agent.chat_backend import build_chat_fn  # noqa: E402
from services.agent.conversation import ConversationMemory, ConversationalAgent  # noqa: E402
from services.agent.skills.artifacts import WriteArtifactSkill  # noqa: E402
from services.agent.skills.gallery import gallery_registry  # noqa: E402
from services.agent.skills.memory import register_memory_skills  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def _thresholds(path: Path | None) -> dict[str, float]:
    values = dict(DEFAULT_THRESHOLDS)
    if path and path.is_file():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        configured = doc.get("thresholds", doc)
        if not isinstance(configured, dict):
            raise ValueError(f"{path}: thresholds must be an object")
        for key, value in configured.items():
            if key in values:
                values[key] = float(value)
    return values


def _score_weights(path: Path | None) -> dict[str, float]:
    defaults = {
        "task_success": 0.40,
        "quality": 0.30,
        "cost": 0.15,
        "trajectory": 0.15,
    }
    if not path or not path.is_file():
        return defaults
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    configured = doc.get("score_weights") or {}
    if not isinstance(configured, dict):
        raise ValueError(f"{path}: score_weights must be an object")
    out = {**defaults, **{key: float(value) for key, value in configured.items() if key in defaults}}
    if any(value < 0 for value in out.values()) or sum(out.values()) <= 0:
        raise ValueError(f"{path}: score_weights must be non-negative with a positive sum")
    return out


def _session_source(case: dict[str, Any]) -> Path:
    explicit = case.get("session_path")
    if explicit:
        path = Path(str(explicit))
        return path if path.is_absolute() else ROOT / path
    return session_dir(str(case.get("session") or "smoke"))


def _analyzed_photo_count(base_dir: Path) -> int | None:
    path = base_dir / "analysis_results.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("photos", "results", "images"):
            if isinstance(value.get(key), list):
                return len(value[key])
    return None


def _model(config_path: str) -> tuple[dict[str, Any], dict[str, Any]]:
    config = ConfigLoader.load(config_path)
    model_cfg = ConfigLoader.get_model_config(config)
    model_name = str(
        model_cfg.get("agent_chat_model") or model_cfg.get("model_name") or "llava"
    )
    return model_cfg, {
        "provider": str(model_cfg.get("provider") or "ollama"),
        "model_name": model_name,
    }


def _inference_usage(tool_spans: list[dict[str, Any]]) -> dict[str, Any]:
    calls = 0
    queue_wait_ms = 0.0
    seen_signal = False
    for span in tool_spans:
        output = span.get("output")
        if not isinstance(output, dict):
            continue
        metadata = output.get("metadata") or {}
        if "inference_calls" in metadata:
            calls += int(metadata.get("inference_calls") or 0)
            seen_signal = True
        if "queue_wait_ms" in metadata:
            queue_wait_ms += float(metadata.get("queue_wait_ms") or 0)
            seen_signal = True
    return {
        "calls": calls,
        "queue_wait_ms": round(queue_wait_ms, 3),
        "available": seen_signal,
        "note": None if seen_signal else "Gallery skills exposed no VLM inference telemetry",
    }


def _run_case(
    case: dict[str, Any],
    *,
    workspace: Path,
    model_cfg: dict[str, Any],
    model_meta: dict[str, Any],
    native_tools: bool,
    max_tool_rounds: int,
    cost_per_1k_tokens: float,
) -> dict[str, Any]:
    source = _session_source(case)
    if not source.is_dir():
        raise FileNotFoundError(f"case {case['id']}: session directory not found: {source}")
    base_dir = workspace / str(case["id"])
    shutil.copytree(source, base_dir)
    analyzed_photo_count = _analyzed_photo_count(base_dir)

    prefs: dict[str, str] = {}
    registry = gallery_registry(str(base_dir))
    artifact_dir = base_dir / "_artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    registry.register(WriteArtifactSkill(str(artifact_dir), url_prefix="/eval/artifacts"))
    register_memory_skills(
        registry,
        owner=f"agent-eval:{case['id']}",
        persist=lambda key, value: prefs.__setitem__(key, value),
        loader=lambda: dict(prefs),
    )
    system_prompt = build_eval_system_prompt(registry)
    raw_chat = build_chat_fn(
        model_cfg,
        model_name=model_meta["model_name"],
        tools=registry.tool_specs(),
        native_tools=native_tools,
    )
    chat = InstrumentedChat(raw_chat)
    tools = InstrumentedRegistry(registry)
    memory = ConversationMemory(system_prompt=system_prompt, max_tokens=4000)
    working_before: dict[str, Any] = {}
    prefs_before = dict(prefs)
    agent = ConversationalAgent(
        chat,
        memory=memory,
        skills=tools,
        wrap_tool_output=False,
        max_tool_rounds=max_tool_rounds,
        working_memory=working_before,
    )

    started_at = _utc_now()
    t0 = time.monotonic()
    result = None
    error: str | None = None
    try:
        result = agent.chat(str(case["user_input"]))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.monotonic() - t0) * 1000, 3)
    llm_calls = list(chat.calls)
    tool_spans = list(tools.calls)
    token_usage = {
        "prompt_tokens": sum(int(call.get("prompt_tokens") or 0) for call in llm_calls),
        "completion_tokens": sum(int(call.get("completion_tokens") or 0) for call in llm_calls),
        "total_tokens": sum(int(call.get("total_tokens") or 0) for call in llm_calls),
        "source": "estimated",
    }
    token_usage["estimated_cost_usd"] = round(
        token_usage["total_tokens"] / 1000 * cost_per_1k_tokens, 6
    )
    inference = _inference_usage(tool_spans)
    events = list(result.events) if result else list(getattr(agent, "_events", []))
    agent_trace = dict(result.trace) if result else dict(agent.last_trace)
    tool_calls = list(result.tool_calls) if result else [
        event for event in events if event.get("type") == "tool_call"
    ]
    working_after = dict(result.working_memory) if result else dict(agent.working_memory)
    job_ids = [
        str((call.get("metadata") or {}).get("job_id"))
        for call in tool_calls
        if (call.get("metadata") or {}).get("job_id") is not None
    ]
    planner = [
        {"type": "tool_decision", **call["planner_output"]}
        for call in llm_calls
        if call.get("planner_output") is not None
    ]
    if agent_trace.get("rule_id"):
        planner.insert(
            0, {"type": "deterministic_route", "rule_id": agent_trace["rule_id"]}
        )
    execution = {
        "trace_id": agent_trace.get("run_id") or uuid.uuid4().hex,
        "job_id": job_ids[0] if job_ids else None,
        "job_ids": job_ids,
        "started_at": started_at,
        "latency_ms": latency_ms,
        "reply": result.reply if result else "",
        "error": error,
        "agent_trace": agent_trace,
        "planner": planner,
        "thought_summary": None,
        "thought_summary_note": "Not exposed: the harness records decisions, not private chain-of-thought",
        "llm_calls": llm_calls,
        "tool_sequence": [call.get("tool") for call in tool_calls],
        "tool_calls": tool_calls,
        "tool_spans": tool_spans,
        "events": events,
        "reflections": [event for event in events if event.get("type") == "reflection"],
        "retries": [
            event for event in events if event.get("type") in {"parse_repair", "retry"}
        ],
        "memory_changes": memory_delta(
            working_before, working_after, prefs_before, dict(prefs)
        ),
        "runtime_budget": {
            "max_tool_rounds": max_tool_rounds,
            "tool_rounds_used": int(agent_trace.get("rounds_used") or len(tool_calls)),
            "case_limits": dict(
                (case.get("required_behavior") or {}).get("budgets") or {}
            ),
        },
        "token_usage": token_usage,
        "inference_usage": inference,
        "runtime": {
            "queue_wait_ms": inference["queue_wait_ms"],
            "queue_wait_available": inference["available"],
        },
    }
    execution["decision_trace"] = build_decision_trace(
        user_input=str(case["user_input"]),
        analyzed_photo_count=analyzed_photo_count,
        planner=planner,
        tool_spans=tool_spans,
        reply=str(execution["reply"] or ""),
    )
    return {"case": case, "execution": execution, "metrics": evaluate_case(case, execution)}


def evaluate(
    *,
    cases_dir: Path,
    config_path: str,
    case_ids: set[str] | None,
    native_tools: bool,
    max_tool_rounds: int,
    cost_per_1k_tokens: float,
    baseline: dict[str, Any] | None,
    thresholds: dict[str, float],
    golden_dir: Path | None = None,
    score_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_dir, case_ids=case_ids)
    goldens = load_golden_cases(golden_dir)
    cases = [
        {**case, **({"golden": goldens[str(case["id"])]} if str(case["id"]) in goldens else {})}
        for case in cases
    ]
    model_cfg, model_meta = _model(config_path)
    run_id = f"agent-eval-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    started_at = _utc_now()
    started = time.monotonic()
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="livehouse-agent-eval-") as temp:
        workspace = Path(temp)
        for case in cases:
            try:
                rows.append(
                    _run_case(
                        case,
                        workspace=workspace,
                        model_cfg=model_cfg,
                        model_meta=model_meta,
                        native_tools=native_tools,
                        max_tool_rounds=max_tool_rounds,
                        cost_per_1k_tokens=cost_per_1k_tokens,
                    )
                )
            except Exception as exc:
                execution = {
                    "trace_id": uuid.uuid4().hex,
                    "job_id": None,
                    "started_at": _utc_now(),
                    "latency_ms": 0,
                    "reply": "",
                    "error": f"{type(exc).__name__}: {exc}",
                    "agent_trace": {},
                    "planner": [],
                    "thought_summary": None,
                    "llm_calls": [],
                    "tool_calls": [],
                    "tool_spans": [],
                    "decision_trace": [],
                    "events": [],
                    "memory_changes": {},
                    "runtime_budget": {},
                    "token_usage": {"total_tokens": 0, "estimated_cost_usd": 0},
                    "inference_usage": {"calls": 0, "available": False},
                    "runtime": {"queue_wait_ms": 0, "queue_wait_available": False},
                }
                rows.append(
                    {"case": case, "execution": execution, "metrics": evaluate_case(case, execution)}
                )
    metrics = aggregate(rows)
    score = composite_score(metrics, score_weights or {})
    threshold_errors = threshold_failures(metrics, thresholds, score)
    regression = compare(metrics, baseline, thresholds)
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "run_id": run_id,
            "started_at": started_at,
            "completed_at": _utc_now(),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "model": {**model_meta, "native_tools": native_tools},
            "case_count": len(rows),
        },
        "metrics": metrics,
        "score": score,
        "thresholds": thresholds,
        "threshold_failures": threshold_errors,
        "regression": regression,
        "passed": not threshold_errors and bool(regression["passed"]),
        "cases": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases-dir", type=Path, default=ROOT / "agent_eval" / "cases")
    parser.add_argument("--golden-dir", type=Path, default=ROOT / "agent_eval" / "golden")
    parser.add_argument("--case", action="append", default=[], help="Run one case id; repeatable")
    parser.add_argument("--config", default="configs/livehouse.yaml")
    parser.add_argument("--baseline", type=Path, default=ROOT / "agent_eval" / "baseline.json")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--thresholds", type=Path, default=ROOT / "agent_eval" / "config.yaml")
    parser.add_argument("--report", type=Path, default=ROOT / "reports" / "latest.html")
    parser.add_argument(
        "--trace", type=Path, default=ROOT / "reports" / "evaluation_trace.json"
    )
    parser.add_argument(
        "--score", type=Path, default=ROOT / "reports" / "evaluation_score.json"
    )
    parser.add_argument("--save-baseline", action="store_true")
    parser.add_argument("--native-tools", action="store_true")
    parser.add_argument("--max-tool-rounds", type=int, default=3)
    parser.add_argument("--cost-per-1k-tokens", type=float, default=0.0)
    args = parser.parse_args(argv)

    try:
        thresholds = _thresholds(args.thresholds)
        score_weights = _score_weights(args.thresholds)
        baseline = None if args.no_baseline else _load_json(args.baseline)
        report = evaluate(
            cases_dir=args.cases_dir,
            config_path=args.config,
            case_ids=set(args.case) or None,
            native_tools=bool(args.native_tools),
            max_tool_rounds=max(0, args.max_tool_rounds),
            cost_per_1k_tokens=max(0.0, args.cost_per_1k_tokens),
            baseline=baseline,
            thresholds=thresholds,
            golden_dir=args.golden_dir,
            score_weights=score_weights,
        )
        args.trace.parent.mkdir(parents=True, exist_ok=True)
        args.trace.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        args.score.parent.mkdir(parents=True, exist_ok=True)
        args.score.write_text(
            json.dumps(report["score"], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        render_report(report, args.report)
        if args.save_baseline:
            args.baseline.write_text(
                json.dumps(
                    {
                        "schema_version": "agent_evaluation_baseline.v1",
                        "created_at": _utc_now(),
                        "run": report["run"],
                        "metrics": report["metrics"],
                        "score": report["score"],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    except Exception as exc:
        print(f"agent evaluation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    behavior = report["metrics"]["behavior"]
    print(
        f"agent_eval: {behavior['passed']}/{behavior['total']} behavior contracts passed "
        f"success={behavior['success_rate']} intent={behavior['intent_accuracy']} "
        f"score={report['score']['overall_score']} report={args.report}"
    )
    if report["threshold_failures"]:
        print(f"threshold failures: {report['threshold_failures']}", file=sys.stderr)
    if report["regression"]["regressions"]:
        print(f"regressions: {report['regression']['regressions']}", file=sys.stderr)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

