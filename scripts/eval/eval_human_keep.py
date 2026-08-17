#!/usr/bin/env python3
"""Score existing predictions against human_keep_v1 (no new labels).

Uses deliverable keep from selection_v1 sample_type. overall/dims on that
file are null, so this reports selection P/R@K only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.labels import join_labels_predictions, load_labels, load_predictions
from scripts.eval.protocol import stamp_protocol
from scripts.eval_stage3 import build_report, print_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        default=str(ROOT / "data" / "eval" / "human_keep_v1" / "labels.jsonl"),
    )
    parser.add_argument("--predictions", required=True)
    parser.add_argument(
        "--json",
        default=str(ROOT / "reports" / "eval" / "human_keep_v1" / "selection_metrics.json"),
    )
    parser.add_argument("--topk", default="5,10")
    parser.add_argument("--split", choices=("canary", "open", "holdout", "all"), default="all")
    parser.add_argument("--accept-holdout", action="store_true")
    parser.add_argument(
        "--splits",
        default=str(ROOT / "data" / "eval" / "selection_v1" / "prompt_ab_splits.json"),
    )
    args = parser.parse_args(argv)

    labels_path = Path(args.labels)
    preds_path = Path(args.predictions)
    if not labels_path.is_file():
        print(f"error: missing {labels_path} — run build_human_keep_v1.py", file=sys.stderr)
        return 2
    if not preds_path.is_file():
        print(f"error: missing predictions {preds_path}", file=sys.stderr)
        return 2

    labels = load_labels(labels_path)
    preds = load_predictions(preds_path)
    if args.split != "all":
        from scripts.eval.prompt_ab import (
            HoldoutSealedError,
            files_for_split,
            filter_labels,
            filter_predictions,
            load_splits,
            require_split,
        )

        try:
            require_split(args.split, accept_holdout=args.accept_holdout)
        except HoldoutSealedError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        files = files_for_split(load_splits(Path(args.splits)), args.split)
        labels = filter_labels(labels, files)
        preds = filter_predictions(preds, files)
    joined = join_labels_predictions(labels, preds)
    if joined.n_matched == 0:
        print("error: 0 matched pairs", file=sys.stderr)
        return 2
    topks = [int(x) for x in str(args.topk).split(",") if x.strip()]
    report = build_report(joined, topks)
    stamp_protocol(
        report,
        labels_path=labels_path,
        predictions_path=preds_path,
        extra={"suite": "human_keep_v1_selection", "keep_source": "selection_v1_sample_type"},
    )
    print_report(report)
    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
