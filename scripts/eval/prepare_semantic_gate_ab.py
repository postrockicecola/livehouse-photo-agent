#!/usr/bin/env python3
"""Build a deterministic balanced file list from latest semantic reviews."""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--negatives", type=int, default=16)
    parser.add_argument("--seed", type=int, default=20260810)
    args = parser.parse_args()
    latest = {}
    for line in args.reviews.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[str(row["file"])] = row
    positives = sorted(
        file_id
        for file_id, row in latest.items()
        if row.get("disposition") == "semantic_reject"
    )
    negatives = sorted(
        file_id
        for file_id, row in latest.items()
        if row.get("disposition") == "pass"
    )
    rng = random.Random(args.seed)
    rng.shuffle(negatives)
    selected = [*positives, *sorted(negatives[: args.negatives])]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(f"Wrote {len(selected)} files ({len(positives)} positive)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
