#!/usr/bin/env python3
"""Train the curation allocation policy with REINFORCE — a minimal RL closed loop.

This turns ``scripts/eval/eval_agent_selection.py`` (a *static* comparison of fixed
planners) into a *learning* loop: a softmax-over-features policy collects its own
rollouts, is rewarded for spending the analyze budget on human keepers, and updates
itself — ``ROLLOUT -> REWARD -> FEEDBACK`` — over many iterations.

It uses the same inputs as the eval harness (human labels + a Stage3 predictions file
as the deterministic analyze oracle + an optional Stage2 ``fast_score`` manifest), so
the trained policy's ``analyzed_keeper_recall`` is directly comparable to the
heuristic / random / oracle baselines.

The default policy has a single feature, the production cheap signal ``fast_score``,
and is *initialised to the production heuristic* (prefer-high-fast_score, ``--init 1``).
Training then discovers what the eval already hinted at — that greedy-high-fast_score
is a poor keeper proxy — and reweights accordingly; the headline is the learning curve
of greedy recall vs that starting point and the random/oracle reference arms.

Example::

    python scripts/rl/train_curation_policy.py \
      --labels data/eval/labels.jsonl \
      --predictions reports/eval/baseline_v4_stage1_two_merged_predictions.json \
      --features data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl \
      --budget 40 --iterations 60 --batch 12 --out reports/rl/curation_policy.json
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.eval_agent_selection import (  # noqa: E402
    load_fast_scores,
    load_oracle_scores,
    load_truth,
)
from services.agent.rl import (  # noqa: E402
    CandidateSpec,
    CurationEnvironment,
    LinearSoftmaxPolicy,
    LoggingEventSink,
    REINFORCETrainer,
    TrainConfig,
)
from services.agent.types import AgentConfig  # noqa: E402

logger = logging.getLogger("train_curation_policy")


def _parse_extra_features(specs: list[str]) -> dict[str, str]:
    """``name=path`` pairs → {feature_name: predictions_path}."""
    out: dict[str, str] = {}
    for item in specs or []:
        if "=" not in item:
            raise SystemExit(f"--extra-feature must be name=path, got {item!r}")
        name, path = item.split("=", 1)
        out[name.strip()] = path.strip()
    return out


def build_synthetic_environment(args) -> tuple[CurationEnvironment, dict[str, dict], list[str]]:
    """A self-contained environment with no data files: keepers are the LOWEST
    fast_score photos, so a prefer-high init must *learn* to recover them. Used by the
    in-cluster Operator demo (`deploy/operator/`) so the Job needs nothing mounted.
    """
    n = max(4, args.synthetic_n)
    k = max(1, min(args.synthetic_keepers, n - 1))
    files = [f"img{i:04d}" for i in range(n)]
    keepers = set(files[:k])  # lowest fast_score == keeper
    specs = [CandidateSpec(image_id=files[i], features={"fast_score": float(i)}) for i in range(n)]
    # Reference oracle arm: rank keepers first (overall high for keepers).
    truth = {f: {"keep": (f in keepers), "overall": (1.0 if f in keepers else 0.0)} for f in files}
    oracle_scores = {files[i]: float(i) for i in range(n)}
    env = CurationEnvironment(specs, keepers=keepers, oracle_scores=oracle_scores)
    return env, truth, files


def build_environment(args) -> tuple[CurationEnvironment, dict[str, dict], list[str]]:
    if args.synthetic:
        return build_synthetic_environment(args)
    truth = load_truth(args.labels)
    oracle_scores = load_oracle_scores(args.predictions)
    fast_scores = load_fast_scores(args.features)
    extra_sources = {name: load_oracle_scores(path) for name, path in _parse_extra_features(args.extra_feature).items()}

    files = sorted(set(truth) & set(oracle_scores))
    if not files:
        raise SystemExit("no overlap between labels and predictions")
    if args.limit and len(files) > args.limit:
        files = sorted(random.Random(args.seed).sample(files, args.limit))

    specs = []
    for f in files:
        fs = fast_scores.get(f, 0.0)
        feats: dict[str, float] = {"fast_score": fs}
        if args.quadratic:
            feats["fast_score_sq"] = fs * fs
        for name, scores in extra_sources.items():
            feats[name] = scores.get(f, 0.0)
        specs.append(CandidateSpec(image_id=f, features=feats))
    keepers = {f for f in files if truth[f]["keep"]}
    env = CurationEnvironment(specs, keepers=keepers, oracle_scores=oracle_scores)
    return env, truth, files


def reference_arms(env: CurationEnvironment, truth: dict[str, dict], files: list[str], budget: int) -> dict[str, float]:
    """Static baselines for context (same definitions as the eval harness)."""
    heuristic = env.fast_score_desc_ids()
    rnd = list(files)
    random.Random(20260626).shuffle(rnd)
    oracle = sorted(files, key=lambda f: truth[f]["overall"], reverse=True)
    return {
        "heuristic_fast_score": env.static_order_recall(heuristic, budget),
        "random": env.static_order_recall(rnd, budget),
        "oracle_by_human_overall": env.static_order_recall(oracle, budget),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default=None)
    ap.add_argument("--predictions", default=None, help="Stage3 predictions JSON used as the deterministic oracle")
    ap.add_argument("--features", default=None, help="Stage2 manifest jsonl for fast_score")
    ap.add_argument("--synthetic", action="store_true",
                    help="use a self-contained synthetic env (no data files); for the Operator demo")
    ap.add_argument("--synthetic-n", type=int, default=120, help="synthetic pool size")
    ap.add_argument("--synthetic-keepers", type=int, default=30, help="synthetic keeper count (lowest fast_score)")
    ap.add_argument("--budget", type=int, default=40, help="analyze calls per episode (< pool size to matter)")
    ap.add_argument("--feature-keys", default="fast_score", help="comma-separated candidate feature keys")
    ap.add_argument("--quadratic", action="store_true", help="add a derived fast_score_sq feature (non-monotone policy)")
    ap.add_argument("--extra-feature", action="append", default=[],
                    help="name=predictions.json — add an external per-image signal as a feature (repeatable)")
    ap.add_argument("--init", type=float, default=None,
                    help="initial weight for ALL features; default = [1,0,..] (start at prefer-high heuristic)")
    ap.add_argument("--temperature", type=float, default=1.0, help="softmax temperature")
    ap.add_argument("--iterations", type=int, default=60)
    ap.add_argument("--batch", type=int, default=12, help="episodes per iteration")
    ap.add_argument("--lr", type=float, default=0.3)
    ap.add_argument("--baseline-decay", type=float, default=0.9)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260626)
    ap.add_argument("--limit", type=int, default=0, help="cap pool to a seeded random sample of N (0 = all)")
    ap.add_argument("--out", default="reports/rl/curation_policy.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.synthetic and (not args.labels or not args.predictions):
        raise SystemExit("--labels and --predictions are required unless --synthetic is set")

    env, truth, files = build_environment(args)
    feature_keys = [k.strip() for k in args.feature_keys.split(",") if k.strip()]
    logger.info(
        "env: %s candidates, %s human keepers; budget=%s; features=%s",
        env.n_candidates, env.n_keepers, args.budget, feature_keys,
    )

    if args.init is not None:
        init_w = np.full(len(feature_keys), args.init, dtype=float)
    else:
        init_w = np.zeros(len(feature_keys))
        init_w[0] = 1.0  # start at the prefer-high heuristic on the first feature
    policy = LinearSoftmaxPolicy(
        feature_keys=feature_keys,
        temperature=args.temperature,
        w=init_w,
    )
    agent_config = AgentConfig(
        target_keepers=args.budget,
        keep_score_threshold=0.0,
        analyze_fast_score_floor=0.0,
        max_inferences=args.budget,
        max_analyze_candidates=args.budget,
        allow_escalation=False,
        max_steps=env.n_candidates + args.budget + 50,
    )

    trainer = REINFORCETrainer(env, policy, agent_config, sink=LoggingEventSink(trace_id="rl-train"))
    result = trainer.train(
        TrainConfig(
            iterations=args.iterations,
            batch=args.batch,
            lr=args.lr,
            baseline_decay=args.baseline_decay,
            eval_every=args.eval_every,
            seed=args.seed,
        )
    )

    arms = reference_arms(env, truth, files, args.budget)
    _print_report(result, arms, args.budget, env.n_candidates, env.n_keepers)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "pool_size": env.n_candidates,
        "human_keepers": env.n_keepers,
        "budget": args.budget,
        "reference_arms": {k: round(v, 4) for k, v in arms.items()},
        "training": result.as_dict(),
    }
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %s", out)
    return 0


def _print_report(result, arms: dict[str, float], budget: int, pool: int, keepers: int) -> None:
    print(f"\n=== RL curation policy training (budget={budget} of {pool}, {keepers} human keepers) ===")
    print("learning curve (greedy analyzed_keeper_recall):")
    print(f"{'iter':>5} {'reward':>8} {'recall':>8} {'baseline':>9} {'grad':>8} {'greedy':>8}  weights")
    for r in result.history:
        greedy = f"{r.greedy_recall:.3f}" if r.greedy_recall is not None else "   .  "
        weights = "[" + ",".join(f"{w:.2f}" for w in r.weights) + "]"
        print(f"{r.iteration:>5} {r.reward_mean:>8.3f} {r.recall_mean:>8.3f} "
              f"{r.baseline:>9.3f} {r.grad_norm:>8.4f} {greedy:>8}  {weights}")
    print("\nreference arms (static allocation):")
    for name, v in arms.items():
        print(f"  {name:<24} recall={v:.3f}")
    print(f"\ninit greedy recall   = {result.init_greedy_recall:.3f}")
    print(f"final greedy recall  = {result.final_greedy_recall:.3f}  "
          f"(Δ {result.final_greedy_recall - result.init_greedy_recall:+.3f})")
    print(f"final weights        = {[round(w, 3) for w in result.final_weights]}  for {result.feature_keys}")


if __name__ == "__main__":
    raise SystemExit(main())
