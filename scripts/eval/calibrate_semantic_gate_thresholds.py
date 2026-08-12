#!/usr/bin/env python3
"""Sweep semantic observation thresholds on a labeled development subset."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_eval.scorers.pipeline_quality import binary_metrics  # noqa: E402


def _rows(path: Path) -> dict[str, dict[str, Any]]:
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[str(row["file"])] = row
    return latest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    reviews = _rows(args.reviews)
    predictions = _rows(args.predictions)
    files = sorted(predictions)
    truth = [
        reviews[file_id].get("disposition") == "semantic_reject"
        for file_id in files
    ]
    candidates = []
    for min_severity in (1, 2, 3):
        for min_confidence in (0.5, 0.6, 0.7, 0.8, 0.9):
            predicted = []
            for file_id in files:
                gate = predictions[file_id].get("semantic_gate") or {}
                predicted.append(
                    bool(gate.get("is_present"))
                    and bool(gate.get("types"))
                    and bool(gate.get("evidence"))
                    and int(gate.get("severity") or 0) >= min_severity
                    and float(gate.get("confidence") or 0) >= min_confidence
                )
            metrics = binary_metrics(truth, predicted)
            candidates.append(
                {
                    "min_severity": min_severity,
                    "min_confidence": min_confidence,
                    "metrics": metrics,
                }
            )
    candidates.sort(
        key=lambda row: (
            row["metrics"]["false_positive_rate"] > 0.10,
            -float(row["metrics"]["recall"]["value"]),
            float(row["metrics"]["false_positive_rate"]),
        )
    )
    report = {
        "schema_version": "semantic_gate_threshold_sweep.v1",
        "sample_size": len(files),
        "positive_count": sum(truth),
        "candidates": candidates,
    }
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(candidates[:5], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
