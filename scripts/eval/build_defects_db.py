#!/usr/bin/env python3
"""Build the zero-tolerance defect blacklist from evaluation labels."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LABELS_PATH = REPO_ROOT / "data" / "eval" / "labels.jsonl"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "data" / "eval" / "agent" / "defects.json"

DIMENSION_REASONS = {
    "focus_sharpness": "out_of_focus",
    "exposure_control": "poor_exposure",
    "deliverable_subject": "undeliverable_subject",
}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_defects_db(labels_path: str | Path) -> dict[str, dict[str, Any]]:
    """Return defect records derived from a JSONL labels file."""
    path = Path(labels_path)
    defects: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")

            photo_id = record.get("file") or record.get("path")
            if not photo_id:
                raise ValueError(f"{path}:{line_number}: missing 'file'")
            photo_id = Path(str(photo_id)).name
            overall_score = _number(
                record.get("overall", record.get("overall_score"))
            )
            dimensions = record.get("dims")
            if not isinstance(dimensions, dict):
                dimensions = record.get("dimensions")
            if not isinstance(dimensions, dict):
                dimensions = record

            reasons: list[str] = []
            if overall_score is not None and overall_score < 45:
                reasons.append("low_overall_score")
            for dimension, reason in DIMENSION_REASONS.items():
                score = _number(dimensions.get(dimension))
                if score is not None and score <= 2:
                    reasons.append(reason)

            if reasons:
                defects[photo_id] = {
                    "is_defect": True,
                    "reasons": reasons,
                    "overall_score": overall_score,
                }
    return defects


def write_defects_db(
    labels_path: str | Path = DEFAULT_LABELS_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> int:
    """Build and write the defect database, returning its record count."""
    defects = build_defects_db(labels_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(defects, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return len(defects)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    count = write_defects_db(args.labels, args.output)
    print(f"Wrote {count} defect records to {args.output}")


if __name__ == "__main__":
    main()
