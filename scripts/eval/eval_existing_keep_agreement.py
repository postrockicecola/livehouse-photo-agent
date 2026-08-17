#!/usr/bin/env python3
"""Agreement among already-written keep columns. No new human labels.

This is not a second-rater IRR. Candidate-round ``keep`` is often Qwen-anchored
(overall≥75); ``human_keep_v1`` is sample_type triage. Low agreement is a
contamination signal, not a human ceiling.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval import metrics as M
from scripts.eval.labels import load_labels

DEFAULT_SOURCES = {
    "human_keep_v1": "data/eval/human_keep_v1/labels.jsonl",
    "round_001_reviews": "data/eval/candidate_rounds/round_001/human_reviews.jsonl",
    "round_002_reviews": "data/eval/candidate_rounds/round_002/human_reviews.jsonl",
}


def _keep_map(path: Path) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for label in load_labels(path):
        if label.key and isinstance(label.keep, bool):
            out[label.key] = bool(label.keep)
    return out


def _pair(name_a: str, map_a: dict[str, bool], name_b: str, map_b: dict[str, bool]) -> dict[str, Any]:
    keys = sorted(set(map_a) & set(map_b))
    if not keys:
        return {
            "a": name_a,
            "b": name_b,
            "n": 0,
            "agreement": None,
            "cohen_kappa": None,
        }
    left = [map_a[k] for k in keys]
    right = [map_b[k] for k in keys]
    agree = sum(1 for x, y in zip(left, right) if x == y) / len(keys)
    return {
        "a": name_a,
        "b": name_b,
        "n": len(keys),
        "agreement": agree,
        "cohen_kappa": M.cohen_kappa(left, right),
        "a_keep_rate": sum(left) / len(left),
        "b_keep_rate": sum(right) / len(right),
    }


def build_report(sources: dict[str, Path]) -> dict[str, Any]:
    loaded: dict[str, dict[str, bool]] = {}
    missing: list[str] = []
    for name, path in sources.items():
        if not path.is_file():
            missing.append(f"{name}:{path}")
            continue
        loaded[name] = _keep_map(path)
    names = list(loaded)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            pairs.append(_pair(a, loaded[a], b, loaded[b]))
    return {
        "schema_version": "keep_source_agreement.v1",
        "independent_second_rater": False,
        "sources": {name: {"path": str(sources[name]), "n_keep_labels": len(loaded[name])} for name in names},
        "missing": missing,
        "pairs": pairs,
        "notes": (
            "human_keep_v1 is selection_v1 sample_type (deliverable vs defect). "
            "Candidate-round keep is a different protocol and may be Qwen-anchored. "
            "Do not quote these pairs as human IRR."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "reports" / "eval" / "human_keep_v1" / "source_agreement.json",
    )
    args = parser.parse_args(argv)
    sources = {name: ROOT / rel for name, rel in DEFAULT_SOURCES.items()}
    report = build_report(sources)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pairs"]:
        print("keep agreement: no overlapping sources", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
