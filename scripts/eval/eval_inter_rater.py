#!/usr/bin/env python3
"""Inter-rater / test–retest agreement between two label JSONL files.

Primary question for the Stage3 Spearman-0.36 story: is ground truth noisy?

    human_spearman ≈ 0.5–0.6  → GT noise; model 0.36 less damning
    human_spearman ≳ 0.8      → GT stable; 0.36 is model/pipeline shortfall

Usage::

    python scripts/eval/eval_inter_rater.py \\
        --r1 data/eval/irr_round2/labels_r1_sealed.jsonl \\
        --r2 data/eval/irr_round2/labels_r2.jsonl \\
        --out reports/eval/inter_rater_round2.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval import metrics as M
from scripts.eval.labels import load_labels, normalize_name


def _verdict(spearman: float) -> str:
    if spearman != spearman:  # NaN
        return "incomplete"
    if spearman < 0.55:
        return "gt_noisy — human ceiling near model; fix labeling protocol before chasing model ρ"
    if spearman < 0.75:
        return "gt_moderate_noise — model 0.36 is weak but partly excused; tighten rubric + dims"
    return "gt_stable — human ρ high; Spearman 0.36 is a real model/pipeline gap"


def build_report(r1_path: Path, r2_path: Path) -> dict[str, Any]:
    r1 = {lb.key: lb for lb in load_labels(r1_path) if lb.key}
    r2 = {lb.key: lb for lb in load_labels(r2_path) if lb.key}
    keys = sorted(set(r1) & set(r2))

    overall_1, overall_2 = [], []
    keep_1, keep_2 = [], []
    for k in keys:
        a, b = r1[k], r2[k]
        if a.overall is not None and b.overall is not None:
            overall_1.append(a.overall)
            overall_2.append(b.overall)
        if isinstance(a.keep, bool) and isinstance(b.keep, bool):
            keep_1.append(a.keep)
            keep_2.append(b.keep)

    icc = M.icc_two_raters(overall_1, overall_2)
    abs_diff = [abs(x - y) for x, y in zip(overall_1, overall_2)]
    within_5 = sum(1 for d in abs_diff if d <= 5) / len(abs_diff) if abs_diff else float("nan")
    within_10 = sum(1 for d in abs_diff if d <= 10) / len(abs_diff) if abs_diff else float("nan")

    keep_agree = (
        sum(1 for a, b in zip(keep_1, keep_2) if a == b) / len(keep_1) if keep_1 else float("nan")
    )

    spearman = M.spearman(overall_1, overall_2)
    report: dict[str, Any] = {
        "task": "inter_rater_agreement",
        "r1": str(r1_path),
        "r2": str(r2_path),
        "n_r1": len(r1),
        "n_r2": len(r2),
        "n_joined": len(keys),
        "n_r1_only": len(set(r1) - set(r2)),
        "n_r2_only": len(set(r2) - set(r1)),
        "overall": {
            "n": len(overall_1),
            "spearman": spearman,
            "pearson": M.pearson(overall_1, overall_2),
            "mae": M.mae(overall_1, overall_2),
            "rmse": M.rmse(overall_1, overall_2),
            "icc2_1": icc["icc2_1"],
            "icc3_1": icc["icc3_1"],
            "pct_abs_diff_le_5": within_5,
            "pct_abs_diff_le_10": within_10,
        },
        "keep": {
            "n": len(keep_1),
            "agreement": keep_agree,
            "cohen_kappa": M.cohen_kappa(keep_1, keep_2) if keep_1 else float("nan"),
        },
        "reference_model_spearman": 0.36,
        "verdict": _verdict(float(spearman) if spearman == spearman else float("nan")),
        "pairs": [
            {
                "file": r1[k].file,
                "r1_overall": r1[k].overall,
                "r2_overall": r2[k].overall,
                "r1_keep": r1[k].keep,
                "r2_keep": r2[k].keep,
                "abs_diff": (
                    abs(r1[k].overall - r2[k].overall)
                    if r1[k].overall is not None and r2[k].overall is not None
                    else None
                ),
            }
            for k in keys
            if r1[k].overall is not None and r2[k].overall is not None
        ],
    }
    # Sort pairs by disagreement for badcase review
    report["pairs"].sort(key=lambda p: (-(p["abs_diff"] or -1), normalize_name(p["file"])))
    return report


def _fmt(x: float | None, nd: int = 3) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x:.{nd}f}"


def print_report(rep: dict[str, Any]) -> None:
    o = rep["overall"]
    k = rep["keep"]
    print(f"joined={rep['n_joined']}  overall_n={o['n']}  keep_n={k['n']}")
    print(
        f"  Spearman {_fmt(o['spearman'])}   Pearson {_fmt(o['pearson'])}   "
        f"MAE {_fmt(o['mae'], 2)}   ICC(2,1) {_fmt(o['icc2_1'])}   ICC(3,1) {_fmt(o['icc3_1'])}"
    )
    print(
        f"  |Δ|≤5 {_fmt(o['pct_abs_diff_le_5'], 2)}   |Δ|≤10 {_fmt(o['pct_abs_diff_le_10'], 2)}   "
        f"keep agree {_fmt(k['agreement'], 2)}   κ {_fmt(k['cohen_kappa'])}"
    )
    print(f"  vs model Spearman 0.36 → {rep['verdict']}")
    hard = [p for p in rep["pairs"] if (p.get("abs_diff") or 0) >= 10][:8]
    if hard:
        print("  largest disagreements:")
        for p in hard:
            print(
                f"    {p['file']}: r1={p['r1_overall']} r2={p['r2_overall']} "
                f"|Δ|={p['abs_diff']} keep {p['r1_keep']}→{p['r2_keep']}"
            )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--r1", required=True, help="sealed / original labels JSONL")
    ap.add_argument("--r2", required=True, help="blind re-label JSONL")
    ap.add_argument("--out", default="", help="optional JSON report path")
    args = ap.parse_args(argv)

    r1 = Path(args.r1)
    r2 = Path(args.r2)
    if not r1.is_file():
        print(f"error: r1 not found: {r1}", file=sys.stderr)
        return 2
    if not r2.is_file():
        print(f"error: r2 not found: {r2}", file=sys.stderr)
        return 2

    # Empty r2 → incomplete
    if r2.stat().st_size == 0:
        print("error: r2 is empty — finish blind labeling first", file=sys.stderr)
        return 2

    rep = build_report(r1, r2)
    print_report(rep)

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        # Drop bulky pairs from default? Keep them — n≈40 is fine.
        out.write_text(json.dumps(rep, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
