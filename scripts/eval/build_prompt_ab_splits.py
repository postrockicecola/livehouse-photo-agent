#!/usr/bin/env python3
"""Write sealed holdout + 16-frame canary splits for prompt A/B."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.prompt_ab import CANARY_N, HOLDOUT_N, write_splits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data" / "eval" / "selection_v1",
    )
    parser.add_argument("--seed", type=int, default=20260817)
    args = parser.parse_args(argv)
    if not (args.dataset / "frozen_manifest.json").is_file():
        print(f"error: missing {args.dataset / 'frozen_manifest.json'}", file=sys.stderr)
        return 2
    splits = write_splits(args.dataset, seed=args.seed)
    print(json.dumps({"counts": splits["counts"], "path": str(args.dataset / "prompt_ab_splits.json")}, indent=2))
    if splits["counts"]["holdout"] != HOLDOUT_N or splits["counts"]["canary"] != CANARY_N:
        print("error: unexpected split sizes", file=sys.stderr)
        return 1
    overlap = set(splits["holdout"]) & set(splits["canary"])
    if overlap:
        print(f"error: holdout/canary overlap {sorted(overlap)[:5]}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
