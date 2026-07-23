#!/usr/bin/env python3
"""Stage3 scoring evaluation harness (P0 baseline).

Quantifies how well AI Stage3 scores agree with human judgement, so prompt/
calibration changes can be compared instead of eyeballed.

Usage
-----
1) Generate a labeling skeleton from existing predictions::

       python scripts/eval_stage3.py template \\
           --predictions analysis_results.json --out data/eval/labels.jsonl

   Then open the JSONL and fill ``overall`` (0-100), ``dims`` (0-10), ``keep``.

2) Score AI vs your labels::

       python scripts/eval_stage3.py run \\
           --labels data/eval/labels.jsonl --predictions analysis_results.json

Metrics: overall Spearman/Pearson/MAE/RMSE, per-dimension MAE/Spearman,
calibration A/B (raw vs calibrated dims), and selection precision/recall@k.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval import metrics as M
from scripts.eval.labels import (
    DIM_KEYS,
    Joined,
    join_labels_predictions,
    load_labels,
    load_predictions,
    make_label_template,
)

DEFAULT_PREDICTIONS = "analysis_results.json"


def _fmt(x: float | None, nd: int = 3) -> str:
    if x is None:
        return "  n/a"
    try:
        if x != x:  # NaN
            return "  n/a"
    except TypeError:
        return "  n/a"
    return f"{x:.{nd}f}"


def build_report(joined: Joined, topks: list[int]) -> dict[str, Any]:
    pairs = joined.pairs

    # --- overall ---
    lo = [lb.overall for lb, p in pairs if lb.overall is not None and p.overall is not None]
    po = [p.overall for lb, p in pairs if lb.overall is not None and p.overall is not None]
    overall = {
        "n": len(lo),
        "spearman": M.spearman(lo, po),
        "pearson": M.pearson(lo, po),
        "mae": M.mae(lo, po),
        "rmse": M.rmse(lo, po),
    }

    # --- per-dimension (calibrated) ---
    per_dim: dict[str, dict[str, Any]] = {}
    for k in DIM_KEYS:
        la = [lb.dims[k] for lb, p in pairs if k in lb.dims and k in p.dims_cal]
        pa = [p.dims_cal[k] for lb, p in pairs if k in lb.dims and k in p.dims_cal]
        per_dim[k] = {
            "n": len(la),
            "mae": M.mae(la, pa),
            "spearman": M.spearman(la, pa),
        }
    dim_maes = [d["mae"] for d in per_dim.values() if d["n"] > 0 and d["mae"] == d["mae"]]
    macro_dim_mae = sum(dim_maes) / len(dim_maes) if dim_maes else float("nan")

    # --- calibration A/B (raw vs calibrated, against human dims) ---
    calib: dict[str, dict[str, Any]] = {}
    for k in DIM_KEYS:
        lab, raw, cal = [], [], []
        for lb, p in pairs:
            if k in lb.dims and k in p.dims_raw and k in p.dims_cal:
                lab.append(lb.dims[k])
                raw.append(p.dims_raw[k])
                cal.append(p.dims_cal[k])
        if lab:
            mae_raw = M.mae(lab, raw)
            mae_cal = M.mae(lab, cal)
            calib[k] = {
                "n": len(lab),
                "mae_raw": mae_raw,
                "mae_cal": mae_cal,
                "delta": mae_cal - mae_raw,  # negative = calibration helped
            }

    # --- selection precision/recall@k ---
    sel_scores = [p.overall for lb, p in pairs if lb.keep is not None and p.overall is not None]
    sel_pos = [bool(lb.keep) for lb, p in pairs if lb.keep is not None and p.overall is not None]
    selection: dict[str, Any] = {"n": len(sel_scores), "n_positives": int(sum(sel_pos))}
    if sel_scores:
        m_keep, m_drop, gap = M.group_mean_separation(sel_scores, sel_pos)
        selection.update({"mean_pred_keep": m_keep, "mean_pred_discard": m_drop, "score_gap": gap})
        at_k = []
        for k in topks:
            r = M.precision_recall_at_k(sel_scores, sel_pos, k)
            at_k.append({"k": r.k, "precision": r.precision, "recall": r.recall, "overlap": r.overlap})
        selection["at_k"] = at_k

    # --- bias / quintile calibration ---
    bias = M.bias_stats(lo, po) if lo else {}
    quintile_calib = M.quintile_calibration(lo, po) if lo else []

    return {
        "matched": joined.n_matched,
        "labels_unmatched": joined.labels_only,
        "predictions_unmatched_count": len(joined.preds_only),
        "overall": overall,
        "per_dimension": per_dim,
        "macro_dim_mae": macro_dim_mae,
        "calibration": calib,
        "selection": selection,
        "bias": bias,
        "quintile_calibration": quintile_calib,
    }


def print_report(rep: dict[str, Any]) -> None:
    line = "=" * 64
    print(line)
    print("Stage3 Evaluation Report")
    print(line)
    print(f"matched pairs            : {rep['matched']}")
    if rep["labels_unmatched"]:
        shown = ", ".join(rep["labels_unmatched"][:8])
        more = "..." if len(rep["labels_unmatched"]) > 8 else ""
        print(f"labels w/o prediction    : {len(rep['labels_unmatched'])} ({shown}{more})")
    print(f"predictions w/o label    : {rep['predictions_unmatched_count']}")

    o = rep["overall"]
    print(f"\n[Overall score] (0-100, n={o['n']})")
    print(f"  Spearman : {_fmt(o['spearman'])}   Pearson : {_fmt(o['pearson'])}")
    print(f"  MAE      : {_fmt(o['mae'], 2)}   RMSE    : {_fmt(o['rmse'], 2)}")

    print(f"\n[Per-dimension] (0-10)   macro-MAE={_fmt(rep['macro_dim_mae'], 2)}")
    print(f"  {'dimension':<22}{'n':>4}  {'MAE':>6}  {'Spearman':>9}")
    for k, d in rep["per_dimension"].items():
        print(f"  {k:<22}{d['n']:>4}  {_fmt(d['mae'], 2):>6}  {_fmt(d['spearman']):>9}")

    if rep["calibration"]:
        print("\n[Calibration A/B] per-dim MAE vs human (raw -> calibrated)")
        print(f"  {'dimension':<22}{'n':>4}  {'raw':>6}  {'cal':>6}  {'delta':>7}")
        helped = 0
        for k, c in rep["calibration"].items():
            flag = " *" if c["delta"] < 0 else ""
            if c["delta"] < 0:
                helped += 1
            print(
                f"  {k:<22}{c['n']:>4}  {_fmt(c['mae_raw'], 2):>6}  "
                f"{_fmt(c['mae_cal'], 2):>6}  {_fmt(c['delta'], 2):>7}{flag}"
            )
        print(f"  ('*' = calibration reduced error; helped {helped}/{len(rep['calibration'])} dims)")
    else:
        print("\n[Calibration A/B] skipped (predictions lack dimensions_raw)")

    s = rep["selection"]
    print(f"\n[Selection] (keep vs discard, n={s['n']}, positives={s.get('n_positives', 0)})")
    if s.get("at_k"):
        print(
            f"  mean pred score: keep={_fmt(s.get('mean_pred_keep'), 1)} "
            f"discard={_fmt(s.get('mean_pred_discard'), 1)} gap={_fmt(s.get('score_gap'), 1)}"
        )
        for r in s["at_k"]:
            print(
                f"  @{r['k']:<3} precision={_fmt(r['precision'], 2)} "
                f"recall={_fmt(r['recall'], 2)} (overlap={r['overlap']})"
            )
    else:
        print("  skipped (no 'keep' labels)")

    bias = rep.get("bias") or {}
    if bias.get("n", 0) >= 2:
        print(f"\n[Bias] (model − human, n={bias['n']})")
        print(f"  mean={_fmt(bias.get('mean_bias'), 2):>7}  median={_fmt(bias.get('median_bias'), 2):>7}"
              f"  over={bias.get('pct_overscored', 0):.0f}%  under={bias.get('pct_underscored', 0):.0f}%")

    qc = rep.get("quintile_calibration") or []
    if qc:
        print(f"\n[Quintile calibration]")
        print(f"  {'Q':<3}  {'range':<9}  {'n':>4}  {'human_μ':>8}  {'model_μ':>8}  {'bias':>7}")
        for q in qc:
            print(
                f"  Q{q['quintile']:<2}  {q['range']:<9}  {q['n']:>4}"
                f"  {q['human_mean']:>8.1f}  {q['model_mean']:>8.1f}  {q['bias_mean']:>+7.1f}"
            )

    print(line)


def cmd_run(args: argparse.Namespace) -> int:
    labels = load_labels(args.labels)
    preds = load_predictions(args.predictions)
    if not labels:
        print(f"No labels loaded from {args.labels}", file=sys.stderr)
        return 2
    joined = join_labels_predictions(labels, preds)
    if joined.n_matched == 0:
        print(
            f"0 matched pairs. Loaded {len(labels)} labels, {len(preds)} predictions.\n"
            "Check that filenames align (suffixes like _rendered are stripped automatically).",
            file=sys.stderr,
        )
        return 2
    topks = [int(x) for x in str(args.topk).split(",") if x.strip()]
    rep = build_report(joined, topks)
    from scripts.eval.protocol import stamp_protocol

    stamp_protocol(
        rep,
        labels_path=args.labels,
        predictions_path=args.predictions,
        config_path=getattr(args, "config", None) or "configs/eval_stage3.yaml",
        seed=getattr(args, "seed", None),
    )
    print_report(rep)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=2)
        print(f"\nReport written to {args.json}")

    if args.output_md:
        from scripts.eval.report import render_markdown
        md = render_markdown(
            rep, meta={"labels_path": args.labels, "predictions_path": args.predictions}
        )
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(md, encoding="utf-8")
        print(f"Markdown report written to {args.output_md}")
    return 0


def cmd_template(args: argparse.Namespace) -> int:
    preds = load_predictions(args.predictions)
    rows = make_label_template(preds, prefill=args.prefill)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    mode = "prefilled (AI values)" if args.prefill else "blank (no anchoring)"
    print(f"Wrote {len(rows)} label rows [{mode}] to {out}")
    print("Next: fill overall (0-100), dims (0-10), keep (true/false), then run:")
    print(f"  python scripts/eval_stage3.py run --labels {out} --predictions {args.predictions}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage3 scoring evaluation harness")
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("run", help="evaluate AI predictions against labels")
    pr.add_argument("--labels", required=True, help="ground-truth labels JSONL")
    pr.add_argument("--predictions", default=DEFAULT_PREDICTIONS, help="predictions JSON (default: %(default)s)")
    pr.add_argument("--topk", default="3,5,10", help="comma-separated k values for selection metrics")
    pr.add_argument("--json", default=None, help="optional path to dump the report as JSON")
    pr.add_argument("--output-md", default=None, dest="output_md",
                    help="optional path to write a Markdown report (e.g. reports/eval/run.md)")
    pr.set_defaults(func=cmd_run)

    pt = sub.add_parser("template", help="generate a labeling skeleton from predictions")
    pt.add_argument("--predictions", default=DEFAULT_PREDICTIONS, help="predictions JSON (default: %(default)s)")
    pt.add_argument("--out", required=True, help="output labels JSONL path")
    pt.add_argument("--prefill", action="store_true", help="seed AI scores (faster but risks anchoring bias)")
    pt.set_defaults(func=cmd_template)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
