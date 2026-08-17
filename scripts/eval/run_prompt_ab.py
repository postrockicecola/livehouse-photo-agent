#!/usr/bin/env python3
"""Compare two Stage3 prediction sets. Decision ignores Spearman.

    python scripts/eval/run_prompt_ab.py \\
        --baseline reports/eval/selection_v1/full_vlm_baseline/predictions.jsonl \\
        --candidate reports/eval/prompt_ab/v10/predictions.jsonl \\
        --split canary

Holdout is sealed unless ``--accept-holdout`` is set for a one-shot accept.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.prompt_ab import (
    DEFAULT_SPLITS,
    HoldoutSealedError,
    compare_prediction_files,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--labels",
        type=Path,
        default=ROOT / "data" / "eval" / "human_keep_v1" / "labels.jsonl",
    )
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--split", choices=("canary", "open", "holdout"), default="canary")
    parser.add_argument(
        "--accept-holdout",
        action="store_true",
        help="required to score the sealed holdout; do not use while iterating prompts",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=ROOT / "reports" / "eval" / "prompt_ab" / "latest.json",
    )
    args = parser.parse_args(argv)
    try:
        report = compare_prediction_files(
            baseline_path=args.baseline,
            candidate_path=args.candidate,
            labels_path=args.labels,
            splits_path=args.splits,
            split=args.split,
            accept_holdout=args.accept_holdout,
        )
    except HoldoutSealedError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decision = report["decision"]
    print(f"split={report['split']} n={report['n_files']} winner={decision['winner']}")
    for check in decision.get("checks") or []:
        print(
            f"  {check.get('result'):<4} {check.get('id')}: "
            f"{check.get('actual')} vs {check.get('threshold')}"
        )
    print(f"diagnostics (ignored): {report['candidate'].get('diagnostics')}")
    print(f"wrote {args.json}")
    return 0 if decision.get("result") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
