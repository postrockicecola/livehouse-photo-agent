#!/usr/bin/env python3
"""Offline head-to-head: production Stage3 gating vs full-VLM on the same labels.

Does **not** re-run the VLM. It replays production ``apply_stage3_candidates_gating``
on a Stage2 eligible manifest, then scores:

- ``full_vlm`` — every labeled image keeps its recorded Stage3 overall score
- ``two_stage_gated`` — only gate-admitted images keep Stage3 scores; others are
  unscored for ranking (selection draws only from the admitted pool)

This fills the README / meta.json gap for the production two-stage path without
requiring another GPU pass.

Example::

    python scripts/eval/eval_two_stage_gating.py \\
      --labels data/eval/labels.jsonl \\
      --predictions data/eval/images/analysis_results.json \\
      --stage2-features data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl \\
      --config configs/livehouse.yaml \\
      --out reports/eval/two_stage_gating.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import metrics as M  # noqa: E402
from scripts.eval.protocol import stamp_protocol  # noqa: E402
from services.processor.pipeline_image_ops import apply_stage3_candidates_gating  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402

logger = logging.getLogger("eval_two_stage_gating")


def load_truth(labels_path: str | Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in Path(labels_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        out[str(r["file"])] = {
            "overall": float(r["overall"]) if r.get("overall") is not None else None,
            "keep": bool(r.get("keep")) if r.get("keep") is not None else None,
        }
    return out


def load_predictions(path: str | Path) -> dict[str, float]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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


def load_stage2_rows(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        fn = r.get("file_name") or r.get("file")
        if fn is None:
            continue
        rows.append(
            {
                "file_name": str(fn),
                "tech_score": float(r.get("tech_score") or 0.0),
                "fast_score": float(r.get("fast_score") or 0.0),
                "debug_info": r.get("debug_info") or {},
            }
        )
    return rows


def _selection_at_k(
    scored: list[tuple[str, float]],
    keepers: set[str],
    k: int,
) -> dict[str, Any]:
    eff_k = min(k, len(scored))
    top = scored[:eff_k]
    hits = sum(1 for name, _ in top if name in keepers)
    return {
        "k": k,
        "effective_k": eff_k,
        "precision": round(hits / eff_k, 4) if eff_k else None,
        "recall": round(hits / len(keepers), 4) if keepers else None,
        "hits": hits,
        "pool_smaller_than_k": len(scored) < k,
    }


def _arm_metrics(
    *,
    name: str,
    scored_names: list[str],
    scores: dict[str, float],
    truth: dict[str, dict[str, Any]],
    topks: list[int],
    vlm_calls: int,
    n_total: int,
) -> dict[str, Any]:
    lo = [truth[f]["overall"] for f in scored_names if truth[f]["overall"] is not None]
    po = [scores[f] for f in scored_names if truth[f]["overall"] is not None]
    keepers = {f for f, t in truth.items() if t.get("keep")}
    ranked = sorted(((f, scores[f]) for f in scored_names), key=lambda x: -x[1])
    at_k = {str(k): _selection_at_k(ranked, keepers, k) for k in topks}
    keeper_in_pool = len(keepers & set(scored_names))
    return {
        "arm": name,
        "scored_count": len(scored_names),
        "vlm_calls": vlm_calls,
        "vlm_call_share": round(vlm_calls / n_total, 4) if n_total else None,
        "keeper_coverage": round(keeper_in_pool / len(keepers), 4) if keepers else None,
        "keeper_in_pool": keeper_in_pool,
        "overall": {
            "n": len(lo),
            "spearman": M.spearman(lo, po) if len(lo) >= 2 else None,
            "pearson": M.pearson(lo, po) if len(lo) >= 2 else None,
            "mae": M.mae(lo, po) if lo else None,
        },
        "precision_recall_at_k": at_k,
    }


def build_report(
    *,
    truth: dict[str, dict[str, Any]],
    predictions: dict[str, float],
    stage2_rows: list[dict[str, Any]],
    config: dict[str, Any],
    topks: list[int],
) -> dict[str, Any]:
    files = sorted(set(truth) & set(predictions))
    n_total = len(files)
    keepers = {f for f in files if truth[f].get("keep")}

    # Restrict Stage2 rows to the labeled/predicted join set.
    stage2_in_eval = [r for r in stage2_rows if str(r["file_name"]) in set(files)]
    kept, skipped, gate_diag = apply_stage3_candidates_gating(
        stage2_in_eval, config=config, batch_input_scale_n=len(stage2_in_eval)
    )
    admitted = {str(r["file_name"]) for r in kept}
    # Images never seen by Stage2 (culled earlier) cannot get Stage3 in prod.
    admitted &= set(files)

    full_arm = _arm_metrics(
        name="full_vlm",
        scored_names=files,
        scores=predictions,
        truth=truth,
        topks=topks,
        vlm_calls=n_total,
        n_total=n_total,
    )
    gated_names = sorted(admitted)
    gated_arm = _arm_metrics(
        name="two_stage_gated",
        scored_names=gated_names,
        scores=predictions,
        truth=truth,
        topks=topks,
        vlm_calls=len(gated_names),
        n_total=n_total,
    )

    return {
        "eval_set_size": n_total,
        "human_keepers": len(keepers),
        "stage2_eligible_in_eval": len(stage2_in_eval),
        "gating": {
            "admitted": len(admitted),
            "skipped_after_gate": len(skipped),
            "diagnostics": {
                k: gate_diag.get(k)
                for k in (
                    "before",
                    "after",
                    "skipped",
                    "gating_source",
                    "pipeline_mode",
                    "stage3_threshold",
                    "top_k_ratio",
                    "effective_threshold",
                    "effective_top_k_ratio",
                    "max_candidates",
                )
                if k in gate_diag or True
            },
        },
        "arms": {
            "full_vlm": full_arm,
            "two_stage_gated": gated_arm,
        },
        "delta": {
            "vlm_calls_saved": n_total - len(admitted),
            "vlm_call_share_drop": round(1.0 - (len(admitted) / n_total), 4) if n_total else None,
            "spearman_full": full_arm["overall"]["spearman"],
            "spearman_gated_on_admitted": gated_arm["overall"]["spearman"],
            "note": (
                "Gated Spearman is computed only on admitted images (honest production "
                "scoring set). Selection@K for gated uses only the admitted pool."
            ),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", default="data/eval/labels.jsonl")
    ap.add_argument(
        "--predictions",
        default="data/eval/images/analysis_results.json",
        help="Full-VLM predictions (same file as stage3_v6_qwen2vl_temp0 baseline)",
    )
    ap.add_argument(
        "--stage2-features",
        default="data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl",
    )
    ap.add_argument("--config", default="configs/livehouse.yaml")
    ap.add_argument("--topk", default="10,20,30")
    ap.add_argument("--seed", type=int, default=20260625)
    ap.add_argument("--out", default="reports/eval/two_stage_gating.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    topks = [int(x) for x in str(args.topk).split(",") if x.strip()]

    truth = load_truth(args.labels)
    predictions = load_predictions(args.predictions)
    stage2_rows = load_stage2_rows(args.stage2_features)
    config = ConfigLoader.load(args.config)

    report = build_report(
        truth=truth,
        predictions=predictions,
        stage2_rows=stage2_rows,
        config=config,
        topks=topks,
    )
    stamp_protocol(
        report,
        labels_path=args.labels,
        predictions_path=args.predictions,
        config_path=args.config,
        seed=args.seed,
        extra={
            "stage2_features": args.stage2_features,
            "method": "offline_replay_apply_stage3_candidates_gating",
            "provenance": "recorded",
        },
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("wrote %s", out)

    full = report["arms"]["full_vlm"]
    gated = report["arms"]["two_stage_gated"]
    print("\n=== two-stage gating vs full-VLM (offline replay) ===")
    print(
        f"n={report['eval_set_size']} keepers={report['human_keepers']} "
        f"admitted={report['gating']['admitted']} "
        f"vlm_share={gated['vlm_call_share']}"
    )
    print(
        f"full    spearman={full['overall']['spearman']} mae={full['overall']['mae']} "
        f"P@20={full['precision_recall_at_k'].get('20', {}).get('precision')}"
    )
    print(
        f"gated   spearman={gated['overall']['spearman']} mae={gated['overall']['mae']} "
        f"P@20={gated['precision_recall_at_k'].get('20', {}).get('precision')} "
        f"keeper_coverage={gated['keeper_coverage']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
