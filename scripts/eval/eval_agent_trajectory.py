#!/usr/bin/env python3
"""Evaluate the curation agent's *trajectory* — how it spends a budget, not just what it picks.

``eval_agent_selection.py`` answers "did the planner analyze/keep the right photos?"
against human labels. This harness answers a different, label-free question:

    Given the same candidates and inference budget, how does each planner *behave*?

It measures the trajectory itself — the numbers a reviewer of an agent (not a
photo product) actually cares about:

- **budget discipline**: inferences used vs the cap, and whether the loop finalized
  on its own or was force-stopped by a guard;
- **structured-output reliability**: ``llm_decision_rate`` (fraction of LLM-attempted
  decisions the model drove) and the heuristic ``fallback_rate`` — the core signal for
  an LLM-first loop with a deterministic safety net;
- **self-correction**: how many reflection-driven escalations fired;
- **efficiency**: steps, analyze/inspect/finalize split, and wall-clock latency.

Runs fully offline: candidates are synthetic (or seeded from a real Stage2 manifest)
and the analyze step is a deterministic fake — no model, no labels, reproducible. The
LLM arm (``--llm``) rides the configured provider; each stochastic arm can be repeated
(``--repeats``) and is reported as mean +/- std with a head-to-head delta vs a baseline.

Example::

    # Offline: LLM-first loop is unavailable without a model, so this compares the
    # deterministic arms and shows the trajectory metric surface.
    python scripts/eval/eval_agent_trajectory.py --synthetic 120 --budget 20 --repeats 3

    # With a real planner LLM (needs ollama / the configured provider):
    python scripts/eval/eval_agent_trajectory.py --synthetic 120 --budget 20 \
        --llm --repeats 5 --out reports/eval/agent_trajectory.json
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.eval_agent_selection import (  # noqa: E402
    RandomAllocationPlanner,
    build_candidates,
    load_fast_scores,
)
from services.agent.loop import CurationAgent  # noqa: E402
from services.agent.planner import HeuristicPlanner, Planner  # noqa: E402
from services.agent.tools import AnalyzeFn, AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry  # noqa: E402
from services.agent.types import AgentConfig, Candidate  # noqa: E402
from utils.stage3_dimensions import STAGE3_DIM_KEYS  # noqa: E402

logger = logging.getLogger("eval_agent_trajectory")


# --------------------------------------------------------------- candidate / analyze fakes


def synthetic_candidates(n: int, *, seed: int) -> list[Candidate]:
    """N candidates with a seeded, reproducible cheap ``fast_score`` (0-100)."""
    rng = random.Random(seed)
    cands: list[Candidate] = []
    for i in range(n):
        fid = f"syn_{i:05d}.jpg"
        cands.append(Candidate(image_id=fid, image_path=fid, features={"fast_score": round(rng.uniform(0, 100), 2)}))
    return cands


def synthetic_analyze_fn(*, seed: int, ambiguous_frac: float = 0.25) -> AnalyzeFn:
    """Deterministic analyze: a stable pseudo-score per image, with some fast-tier
    results landing in the ambiguous band / low confidence so reflection escalations
    actually fire (exercising the self-correction path). ``full`` tier is confident.
    """

    def _fn(image_path: str, tier: str) -> dict[str, Any]:
        fid = Path(image_path).name
        # Stable per-image RNG so repeated analyze of the same image is deterministic.
        r = random.Random(f"{seed}:{fid}")
        base = r.uniform(30, 95)
        if tier == "full":
            return {
                "score": round(base, 2),
                "confidence": 0.95,
                "verdict": "full",
                "dimensions": {k: 7 for k in STAGE3_DIM_KEYS},
            }
        # fast tier: a fraction are deliberately shaky to trigger escalation.
        shaky = r.random() < ambiguous_frac
        return {
            "score": round(70.0 if shaky else base, 2),  # 70 sits in the default ambiguous band
            "confidence": round(0.6 if shaky else 0.9, 2),
            "verdict": "fast",
            "dimensions": {},
        }

    return _fn


# --------------------------------------------------------------- trajectory metrics


def trajectory_metrics(result: Any, *, wall_ms: float) -> dict[str, Any]:
    """Extract the behavioral surface from one agent run (label-free)."""
    m = dict(result.metrics or {})
    actions = dict(m.get("action_counts") or {})
    src = dict(m.get("planner_source_counts") or {})

    llm = int(src.get("llm", 0) or 0)
    fb = int(src.get("llm_fallback", 0) or 0)
    if not fb:
        fb = int(m.get("llm_fallback_calls", 0) or 0)
    llm_total = llm + fb

    used = int(m.get("inferences_used", 0) or 0)
    cap = int(m.get("max_inferences", 0) or 0)

    zero_cost_tools = (
        int(actions.get("compare", 0) or 0)
        + int(actions.get("cluster", 0) or 0)
        + int(actions.get("query_gallery", 0) or 0)
    )

    return {
        "steps": int(m.get("steps", 0) or 0),
        "inspect_steps": int(actions.get("inspect", 0) or 0),
        "analyze_steps": int(actions.get("analyze", 0) or 0),
        "finalize_steps": int(actions.get("finalize", 0) or 0),
        # Zero-cost planning tools (compare / cluster / query_gallery): tool-use richness.
        "zero_cost_tool_calls": zero_cost_tools,
        "inferences_used": used,
        "max_inferences": cap,
        "budget_utilization": round(used / cap, 4) if cap else None,
        "budget_exhausted": bool(m.get("budget_exhausted", False)),
        "escalations": int(m.get("escalations", 0) or 0),
        "candidates_analyzed": int(m.get("candidates_analyzed", 0) or 0),
        "selected_count": int(m.get("selected_count", 0) or 0),
        # Structured-output reliability of an LLM-first loop.
        "llm_decision_rate": m.get("llm_decision_rate"),
        "llm_fallback_calls": fb,
        "fallback_rate": round(fb / llm_total, 4) if llm_total else None,
        "wall_ms": round(wall_ms, 2),
    }


def _registry(analyze_fn: AnalyzeFn, *, default_tier: str) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier=default_tier),
        finalize=FinalizeTool(),
    )


def run_once(planner: Planner, candidates: list[Candidate], analyze_fn: AnalyzeFn, cfg: AgentConfig) -> dict[str, Any]:
    """Run one planner on a fresh copy of the candidates; return trajectory metrics."""
    agent = CurationAgent(tools=_registry(analyze_fn, default_tier=cfg.base_tier), config=cfg, planner=planner)
    t0 = time.perf_counter()
    result = agent.run(copy.deepcopy(candidates))
    wall_ms = (time.perf_counter() - t0) * 1000.0
    return trajectory_metrics(result, wall_ms=wall_ms)


_NUMERIC_KEYS = (
    "steps",
    "inspect_steps",
    "analyze_steps",
    "finalize_steps",
    "zero_cost_tool_calls",
    "inferences_used",
    "budget_utilization",
    "escalations",
    "candidates_analyzed",
    "selected_count",
    "llm_decision_rate",
    "llm_fallback_calls",
    "fallback_rate",
    "wall_ms",
)


def _aggregate(runs: list[dict[str, Any]]) -> dict[str, dict[str, Optional[float]]]:
    """mean / std over repeats for the numeric keys (None values are ignored)."""
    mean: dict[str, Optional[float]] = {}
    std: dict[str, Optional[float]] = {}
    for k in _NUMERIC_KEYS:
        vals = [float(r[k]) for r in runs if r.get(k) is not None]
        if not vals:
            mean[k] = None
            std[k] = None
            continue
        mean[k] = round(statistics.fmean(vals), 4)
        std[k] = round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0
    return {"mean": mean, "std": std}


def evaluate(
    arms: dict[str, Planner],
    candidates: list[Candidate],
    analyze_fn: AnalyzeFn,
    cfg: AgentConfig,
    *,
    repeats: int = 1,
    baseline: str = "heuristic",
) -> dict[str, Any]:
    """Run every arm ``repeats`` times; return per-arm mean/std + deltas vs ``baseline``."""
    reps = max(1, int(repeats))
    arms_out: dict[str, Any] = {}
    for name, planner in arms.items():
        runs = [run_once(planner, candidates, analyze_fn, cfg) for _ in range(reps)]
        agg = _aggregate(runs)
        arms_out[name] = {"repeats": reps, "mean": agg["mean"], "std": agg["std"], "runs": runs}
        mean = agg["mean"]
        logger.info(
            "arm=%-9s budget_util=%s llm_decision_rate=%s fallback_rate=%s escalations=%s wall_ms=%s",
            name,
            mean.get("budget_utilization"),
            mean.get("llm_decision_rate"),
            mean.get("fallback_rate"),
            mean.get("escalations"),
            mean.get("wall_ms"),
        )

    base = arms_out.get(baseline)
    if base is not None:
        base_mean = base["mean"]
        for name, arm in arms_out.items():
            if name == baseline:
                continue
            delta: dict[str, Optional[float]] = {}
            for k in ("budget_utilization", "llm_decision_rate", "escalations", "selected_count", "wall_ms"):
                a = arm["mean"].get(k)
                b = base_mean.get(k)
                delta[k] = round(a - b, 4) if (a is not None and b is not None) else None
            arm["delta_vs_" + baseline] = delta

    return {
        "candidates_total": len(candidates),
        "budget": cfg.max_inferences,
        "repeats": reps,
        "baseline": baseline,
        "arms": arms_out,
    }


# --------------------------------------------------------------- CLI


def build_arms(*, seed: int, include_llm: bool, config_path: str, llm_model: Optional[str], llm_window: int) -> dict[str, Planner]:
    arms: dict[str, Planner] = {
        "random": RandomAllocationPlanner(seed=seed),
        "heuristic": HeuristicPlanner(),
    }
    if include_llm:
        from services.agent.llm_backend import build_curation_llm_planner_from_config

        arms["llm"] = build_curation_llm_planner_from_config(
            config_path, model_name=llm_model, max_state_candidates=llm_window
        )
    return arms


def _print_table(report: dict[str, Any]) -> None:
    print(
        f"\n=== agent trajectory eval (budget={report['budget']} of {report['candidates_total']}, "
        f"repeats={report['repeats']}, baseline={report['baseline']}) ==="
    )
    header = f"{'arm':<10} {'budget_util':>11} {'llm_rate':>9} {'fallback':>9} {'escal':>6} {'sel':>5} {'wall_ms':>9}"
    print(header)
    for name, arm in report["arms"].items():
        m = arm["mean"]

        def _f(key: str, width: int, prec: int = 3) -> str:
            v = m.get(key)
            return f"{'—':>{width}}" if v is None else f"{v:>{width}.{prec}f}"

        row = (
            f"{name:<10} {_f('budget_utilization', 11)} {_f('llm_decision_rate', 9)} "
            f"{_f('fallback_rate', 9)} {_f('escalations', 6, 1)} {_f('selected_count', 5, 0)} "
            f"{_f('wall_ms', 9, 1)}"
        )
        print(row)


def main() -> int:
    ap = argparse.ArgumentParser(description="Label-free trajectory eval for the curation agent.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--synthetic", type=int, default=120, help="generate N seeded synthetic candidates (default)")
    src.add_argument("--features", default=None, help="Stage2 manifest jsonl for real fast_score candidates")
    ap.add_argument("--budget", type=int, default=20, help="max VLM analyze calls (must be < N to matter)")
    ap.add_argument("--keepers", type=int, default=15, help="target_keepers for finalize")
    ap.add_argument("--keep-threshold", type=float, default=70.0, help="min analyze score to keep")
    ap.add_argument("--repeats", type=int, default=1, help="runs per arm (mean/std); useful for the stochastic LLM arm")
    ap.add_argument("--baseline", default="heuristic", help="arm to compute deltas against")
    ap.add_argument("--no-escalation", action="store_true", help="disable reflection escalation")
    ap.add_argument("--seed", type=int, default=20260712)
    ap.add_argument("--llm", action="store_true", help="also evaluate the real LLM planner (stochastic, needs a model)")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--llm-window", type=int, default=60)
    ap.add_argument("--config", default="configs/livehouse.yaml")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.features:
        fast_scores = load_fast_scores(args.features)
        if not fast_scores:
            logger.error("no fast_score rows in %s", args.features)
            return 2
        candidates = build_candidates(sorted(fast_scores), fast_scores)
    else:
        candidates = synthetic_candidates(int(args.synthetic), seed=args.seed)

    if args.budget >= len(candidates):
        logger.warning("budget (%s) >= candidates (%s): allocation is trivial", args.budget, len(candidates))

    cfg = AgentConfig(
        target_keepers=args.keepers,
        keep_score_threshold=args.keep_threshold,
        max_inferences=args.budget,
        max_analyze_candidates=args.budget,
        allow_escalation=not args.no_escalation,
        max_steps=len(candidates) + args.budget + 50,
    )
    analyze_fn = synthetic_analyze_fn(seed=args.seed)
    arms = build_arms(
        seed=args.seed,
        include_llm=args.llm,
        config_path=args.config,
        llm_model=args.llm_model,
        llm_window=args.llm_window,
    )

    report = evaluate(arms, candidates, analyze_fn, cfg, repeats=args.repeats, baseline=args.baseline)
    _print_table(report)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
