#!/usr/bin/env python3
"""Evaluate semantic-gate predictions against latest human development reviews."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_eval.scorers.pipeline_quality import binary_metrics  # noqa: E402


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def evaluate(reviews_path: Path, predictions_path: Path) -> dict[str, Any]:
    review_rows = _read_jsonl(reviews_path)
    reviews = {
        str(row["file"]): row for row in review_rows if row.get("file")
    }
    predictions = {
        str(row["file"]): row
        for row in _read_jsonl(predictions_path)
        if row.get("file")
    }
    source_unique_reviews = len(reviews)
    if set(predictions) - set(reviews):
        raise ValueError(
            f"predictions without reviews: {sorted(set(predictions) - set(reviews))}"
        )
    reviews = {file_id: reviews[file_id] for file_id in predictions}

    eligible = [
        file_id
        for file_id, row in reviews.items()
        if row.get("disposition") in {"pass", "semantic_reject"}
    ]
    truth = [
        reviews[file_id].get("disposition") == "semantic_reject"
        for file_id in eligible
    ]
    predicted: list[bool] = []
    disagreements: list[dict[str, Any]] = []
    type_truth: Counter[str] = Counter()
    type_caught: Counter[str] = Counter()
    for file_id, expected in zip(eligible, truth):
        row = predictions[file_id]
        gate = row.get("semantic_gate") or row.get("semantic_defect") or {}
        actual = bool(
            gate.get("status") == "reject"
            if gate.get("status") is not None
            else gate.get("is_present")
        )
        predicted.append(actual)
        human_gate = reviews[file_id].get("semantic_gate") or {}
        for defect_type in human_gate.get("types") or []:
            type_truth[str(defect_type)] += 1
            if actual:
                type_caught[str(defect_type)] += 1
        if actual != expected:
            disagreements.append(
                {
                    "file": file_id,
                    "human_disposition": reviews[file_id].get("disposition"),
                    "human_gate": human_gate,
                    "predicted_gate": gate,
                    "error_type": "false_negative" if expected else "false_positive",
                }
            )
    return {
        "schema_version": "semantic_gate_dev_report.v1",
        "review_rows": len(review_rows),
        "source_unique_reviews": source_unique_reviews,
        "unique_reviews": len(reviews),
        "revisions": len(review_rows) - source_unique_reviews,
        "dispositions": dict(
            sorted(Counter(str(row.get("disposition")) for row in reviews.values()).items())
        ),
        "metrics": binary_metrics(truth, predicted),
        "type_recall": {
            defect_type: {
                "caught": type_caught[defect_type],
                "total": total,
                "value": round(type_caught[defect_type] / total, 6),
            }
            for defect_type, total in sorted(type_truth.items())
        },
        "disagreements": disagreements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.reviews, args.predictions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    disagreements_path = args.output.with_name(
        f"{args.output.stem}_disagreements.jsonl"
    )
    with disagreements_path.open("w", encoding="utf-8") as handle:
        for row in report["disagreements"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary = {
        key: report[key]
        for key in ("unique_reviews", "revisions", "dispositions", "metrics", "type_recall")
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
