#!/usr/bin/env python3
"""Evaluate the curation agent's *planner* against human ground truth.

The question this answers is narrow and honest: under a limited VLM analyze budget,
does a given planner spend that budget on the photos a human would actually keep?

To isolate the *planner* (allocation) from the *scorer* (the VLM), the analyze step
is a deterministic oracle: it returns a precomputed Stage3 ``overall_score`` from an
existing predictions file — no live model, fully reproducible. The planners differ
only in *which* candidates they choose to analyze before the budget runs out.

Arms compared:
- ``heuristic`` : production policy (greedy by cheap Stage2 ``fast_score``).
- ``random``    : seeded random allocation — the lower-bound baseline.
- ``oracle``    : analyze by the *true* human ``overall`` — the allocation upper bound.
- ``llm``       : the real :class:`LLMPlanner` (``--llm``; stochastic, needs ollama).

Headline metric — ``analyzed_keeper_recall``: of all human-kept photos, the fraction
the planner actually analyzed (you cannot select what you never looked at). Secondary:
precision/recall@k of the final selection vs human keep labels.

Example::

    python scripts/eval/eval_agent_selection.py \
      --labels data/eval/labels.jsonl \
      --predictions reports/eval/baseline_v4_stage1_two_merged_predictions.json \
      --features data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl \
      --budget 40 --topk 10,30 --out reports/eval/agent_selection.json
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import random
import sys
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.metrics import precision_recall_at_k  # noqa: E402
from services.agent.loop import CurationAgent  # noqa: E402
from services.agent.planner import HeuristicPlanner  # noqa: E402
from services.agent.tools import AnalyzeFn, AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry  # noqa: E402
from services.agent.types import ActionType, AgentConfig, Candidate, ToolCall  # noqa: E402

logger = logging.getLogger("eval_agent_selection")


# --------------------------------------------------------------------------- IO


def load_truth(labels_path: str | Path) -> dict[str, dict[str, Any]]:
    """file -> {overall, keep} from the human label jsonl."""
    out: dict[str, dict[str, Any]] = {}
    for line in Path(labels_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        out[str(r["file"])] = {"overall": float(r.get("overall") or 0.0), "keep": bool(r.get("keep"))}
    return out


def load_oracle_scores(predictions_path: str | Path) -> dict[str, float]:
    """file -> Stage3 overall_score (the deterministic analyze oracle)."""
    data = json.loads(Path(predictions_path).read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("predictions") or data.get("rows") or []
    out: dict[str, float] = {}
    for r in rows:
        f = r.get("file") or r.get("file_name")
        if f is None:
            continue
        score = r.get("overall_score")
        if score is None:
            score = (r.get("scores") or {}).get("overall")
        if score is not None:
            out[str(f)] = float(score)
    return out


def load_fast_scores(features_path: Optional[str | Path]) -> dict[str, float]:
    """file -> Stage2 fast_score from a manifest jsonl (optional)."""
    if not features_path:
        return {}
    out: dict[str, float] = {}
    for line in Path(features_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        f = r.get("file_name") or r.get("file")
        if f is not None and r.get("fast_score") is not None:
            out[str(f)] = float(r["fast_score"])
    return out


# --------------------------------------------------------------- agent plumbing


def build_candidates(files: list[str], fast_scores: dict[str, float]) -> list[Candidate]:
    return [
        Candidate(image_id=f, image_path=f, features={"fast_score": fast_scores.get(f, 0.0)})
        for f in files
    ]


def make_oracle_analyze_fn(scores: dict[str, float]) -> AnalyzeFn:
    """Deterministic analyze: return the precomputed Stage3 score for an image."""

    def _fn(image_path: str, tier: str) -> dict[str, Any]:
        f = Path(image_path).name
        return {"score": scores.get(f, 0.0), "confidence": 0.9, "verdict": "", "dimensions": {}}

    return _fn


class RandomAllocationPlanner:
    """Inspect all, then analyze a *random* un-analyzed candidate — the lower bound."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def next_action(self, state) -> ToolCall:
        ni = state.not_inspected()
        if ni:
            return ToolCall(action=ActionType.INSPECT, image_id=ni[0].image_id, reason="inspect")
        if state.can_analyze_more():
            pool = state.analyzable()
            if pool:
                c = self._rng.choice(pool)
                return ToolCall(action=ActionType.ANALYZE, image_id=c.image_id, args={"tier": "fast"}, reason="random")
        return ToolCall(action=ActionType.FINALIZE, reason="budget spent")


class OracleAllocationPlanner:
    """Inspect all, then analyze the candidate with the highest *true* human score."""

    def __init__(self, truth: dict[str, dict[str, Any]]) -> None:
        self._truth = truth

    def next_action(self, state) -> ToolCall:
        ni = state.not_inspected()
        if ni:
            return ToolCall(action=ActionType.INSPECT, image_id=ni[0].image_id, reason="inspect")
        if state.can_analyze_more():
            pool = state.analyzable()
            if pool:
                c = max(pool, key=lambda x: self._truth.get(x.image_id, {}).get("overall", 0.0))
                return ToolCall(action=ActionType.ANALYZE, image_id=c.image_id, args={"tier": "fast"}, reason="oracle")
        return ToolCall(action=ActionType.FINALIZE, reason="budget spent")


def _registry(analyze_fn: AnalyzeFn) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )


def run_arm(planner, candidates: list[Candidate], analyze_fn: AnalyzeFn, cfg: AgentConfig):
    """Run one planner on a fresh copy of the candidates (the agent mutates state)."""
    agent = CurationAgent(tools=_registry(analyze_fn), config=cfg, planner=planner)
    return agent.run(copy.deepcopy(candidates))


# ------------------------------------------------------------------- scoring


def score_arm(result, truth: dict[str, dict[str, Any]], topks: list[int]) -> dict[str, Any]:
    true_keepers = {f for f, t in truth.items() if t["keep"]}
    analyzed = [c.image_id for c in result.candidates if c.attempts > 0]
    analyzed_set = set(analyzed)

    n_keepers = len(true_keepers) or 1
    analyzed_keeper_recall = len(analyzed_set & true_keepers) / n_keepers

    # Rank the analyzed pool by the agent's (oracle) score for selection metrics.
    scored = [(c.image_id, float(c.score or 0.0)) for c in result.candidates if c.attempts > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    ids = [i for i, _ in scored]
    pred_scores = [s for _, s in scored]
    is_pos = [i in true_keepers for i in ids]

    at_k = {}
    for k in topks:
        pr = precision_recall_at_k(pred_scores, is_pos, k)
        at_k[str(k)] = {"precision": round(pr.precision, 4), "recall": round(pr.recall, 4), "overlap": pr.overlap}

    # Final committed selection (agent's own finalize decision).
    selected = list(result.selected)
    sel_hits = len(set(selected) & true_keepers)
    sel_precision = sel_hits / len(selected) if selected else float("nan")

    return {
        "analyzed_count": len(analyzed),
        "analyzed_keeper_recall": round(analyzed_keeper_recall, 4),
        "selection_size": len(selected),
        "selection_precision": round(sel_precision, 4) if selected else None,
        "selection_keeper_hits": sel_hits,
        "precision_recall_at_k": at_k,
        "metrics": result.metrics,
    }


def build_planners(
    truth: dict[str, dict[str, Any]],
    *,
    seed: int,
    include_llm: bool,
    config_path: str,
    llm_model: Optional[str],
    llm_window: int = 60,
) -> dict[str, Any]:
    arms: dict[str, Any] = {
        "random": RandomAllocationPlanner(seed=seed),
        "heuristic": HeuristicPlanner(),
        "oracle": OracleAllocationPlanner(truth),
    }
    if include_llm:
        from services.agent.llm_backend import build_curation_llm_planner_from_config

        arms["llm"] = build_curation_llm_planner_from_config(
            config_path, model_name=llm_model, max_state_candidates=llm_window
        )
    return arms


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--predictions", required=True, help="Stage3 predictions JSON used as the deterministic oracle")
    ap.add_argument("--features", default=None, help="Stage2 manifest jsonl for fast_score (planner ordering)")
    ap.add_argument("--budget", type=int, default=40, help="max VLM analyze calls (must be < N to matter)")
    ap.add_argument("--keepers", type=int, default=30, help="target_keepers for the agent's finalize")
    ap.add_argument("--keep-threshold", type=float, default=0.0, help="min oracle score to keep (0 = rank only)")
    ap.add_argument("--topk", default="10,30")
    ap.add_argument("--seed", type=int, default=20260625)
    ap.add_argument("--limit", type=int, default=0, help="cap eval set to a seeded random sample of N (0 = all)")
    ap.add_argument("--llm", action="store_true", help="also evaluate the real LLM planner (stochastic, needs ollama)")
    ap.add_argument("--llm-model", default=None)
    ap.add_argument("--llm-window", type=int, default=60, help="how many candidates the LLM planner sees per step")
    ap.add_argument("--config", default="configs/livehouse.yaml")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    topks = [int(x) for x in str(args.topk).split(",") if x.strip()]

    truth = load_truth(args.labels)
    oracle_scores = load_oracle_scores(args.predictions)
    fast_scores = load_fast_scores(args.features)

    # Evaluate only on images we can both score (oracle) and grade (human label).
    files = sorted(set(truth) & set(oracle_scores))
    if not files:
        logger.error("no overlap between labels and predictions")
        return 2
    if args.limit and len(files) > args.limit:
        files = sorted(random.Random(args.seed).sample(files, args.limit))
    n_keepers = sum(1 for f in files if truth[f]["keep"])
    logger.info("eval set: %s images (%s human keepers); budget=%s of %s", len(files), n_keepers, args.budget, len(files))

    candidates = build_candidates(files, fast_scores)
    analyze_fn = make_oracle_analyze_fn(oracle_scores)
    cfg_kwargs = dict(
        target_keepers=args.keepers,
        keep_score_threshold=args.keep_threshold,
        max_inferences=args.budget,
        max_analyze_candidates=args.budget,
        allow_escalation=False,
        max_steps=len(files) + args.budget + 50,
    )

    planners = build_planners(
        truth,
        seed=args.seed,
        include_llm=args.llm,
        config_path=args.config,
        llm_model=args.llm_model,
        llm_window=args.llm_window,
    )
    report: dict[str, Any] = {
        "eval_set_size": len(files),
        "human_keepers": n_keepers,
        "budget": args.budget,
        "arms": {},
    }
    for name, planner in planners.items():
        result = run_arm(planner, candidates, analyze_fn, AgentConfig(**cfg_kwargs))
        report["arms"][name] = score_arm(result, truth, topks)
        logger.info("arm=%-9s analyzed_keeper_recall=%.3f", name, report["arms"][name]["analyzed_keeper_recall"])

    _print_table(report, topks)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("wrote %s", out)
    return 0


def _print_table(report: dict[str, Any], topks: list[int]) -> None:
    print(f"\n=== agent planner allocation eval (budget={report['budget']} of "
          f"{report['eval_set_size']}, {report['human_keepers']} human keepers) ===")
    header = f"{'arm':<10} {'analyzed':>8} {'keeper_recall':>13} {'sel_prec':>9}"
    for k in topks:
        header += f" {'P@'+str(k):>6}"
    print(header)
    for name, m in report["arms"].items():
        row = (
            f"{name:<10} {m['analyzed_count']:>8} {m['analyzed_keeper_recall']:>13.3f} "
            f"{(m['selection_precision'] if m['selection_precision'] is not None else float('nan')):>9.3f}"
        )
        for k in topks:
            row += f" {m['precision_recall_at_k'][str(k)]['precision']:>6.3f}"
        print(row)


if __name__ == "__main__":
    raise SystemExit(main())
