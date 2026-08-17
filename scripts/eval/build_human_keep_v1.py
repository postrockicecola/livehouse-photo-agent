#!/usr/bin/env python3
"""Assemble human-keep labels from selection_v1 triage (not Qwen overall).

keep is taken from frozen human ``sample_type`` (not Qwen overall, not
highlight-only ``provenance.human_keep``):
  highlight | ordinary → keep=true
  technical_hard | semantic_defect → keep=false

This is independent of ``golden_apr_jul_2026`` keep=(overall>=75).
A 100-image stratified IRR queue is written without scores so a second
photographer can label blind.

Usage::

    python scripts/eval/build_human_keep_v1.py
    python scripts/eval/review_human_keep.py
    python scripts/eval/eval_inter_rater.py \\
        --r1 data/eval/human_keep_v1/labels.jsonl \\
        --r2 data/eval/human_keep_v1/labels_r2.jsonl \\
        --out reports/eval/human_keep_v1/irr.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.labels import normalize_name

KEEP_TYPES = frozenset({"ordinary", "highlight"})
DROP_TYPES = frozenset({"technical_hard", "semantic_defect"})
DEFAULT_OUT = ROOT / "data" / "eval" / "human_keep_v1"
IRR_QUOTAS = {
    "technical_hard": 20,
    "semantic_defect": 20,
    "ordinary": 40,
    "highlight": 20,
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _keep_from_type(sample_type: str, provenance: dict[str, Any] | None) -> bool | None:
    """Deliverable keep: ordinary+highlight. Ignore highlight-only provenance.human_keep."""
    del provenance
    if sample_type in KEEP_TYPES:
        return True
    if sample_type in DROP_TYPES:
        return False
    return None


def build(dataset_dir: Path, out_dir: Path, *, seed: int, irr_n: int) -> dict[str, Any]:
    frozen = _read_json(dataset_dir / "frozen_manifest.json")
    labels = {normalize_name(row["file"]): row for row in _read_jsonl(dataset_dir / "labels.jsonl")}
    items: list[dict[str, Any]] = []
    for raw in frozen.get("items") or []:
        file_id = str(raw["file"])
        sample_type = str(raw.get("sample_type") or labels.get(normalize_name(file_id), {}).get("sample_type") or "")
        provenance = raw.get("provenance") if isinstance(raw.get("provenance"), dict) else {}
        keep = _keep_from_type(sample_type, provenance)
        if keep is None:
            continue
        items.append(
            {
                "file": file_id,
                "keep": keep,
                "sample_type": sample_type,
                "session": raw.get("session"),
                "source_path": raw.get("source_path"),
                "orientation_correction_degrees": raw.get("orientation_correction_degrees", 0),
                "provenance": {
                    "origin": "human_triage",
                    "dataset": "selection_v1",
                    "not_qwen_overall": True,
                    "human_reviewed": bool(provenance.get("human_reviewed", True)),
                },
            }
        )

    rng = random.Random(seed)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in items:
        by_type.setdefault(str(row["sample_type"]), []).append(row)
    irr: list[dict[str, Any]] = []
    for sample_type, quota in IRR_QUOTAS.items():
        pool = list(by_type.get(sample_type) or [])
        rng.shuffle(pool)
        irr.extend(pool[:quota])
    if len(irr) < irr_n:
        leftover = [row for row in items if row["file"] not in {r["file"] for r in irr}]
        rng.shuffle(leftover)
        irr.extend(leftover[: irr_n - len(irr)])
    irr = irr[:irr_n]
    irr.sort(key=lambda row: str(row["file"]))

    labels_out = [
        {
            "file": row["file"],
            "keep": row["keep"],
            "overall": None,
            "dims": {},
            "notes": "",
            "sample_type": row["sample_type"],
            "label_schema": "human_keep_v1",
        }
        for row in items
    ]
    queue_out = [
        {
            "file": row["file"],
            "source_path": row["source_path"],
            "orientation_correction_degrees": row["orientation_correction_degrees"],
            "session": row["session"],
        }
        for row in irr
    ]
    r2_template = [
        {"file": row["file"], "keep": None, "notes": "", "label_schema": "human_keep_v1"}
        for row in irr
    ]

    _write_jsonl(out_dir / "labels.jsonl", labels_out)
    _write_jsonl(out_dir / "irr_queue.jsonl", queue_out)
    r2_path = out_dir / "labels_r2.jsonl"
    if not r2_path.is_file():
        _write_jsonl(r2_path, r2_template)

    digest = hashlib.sha256()
    for row in labels_out:
        digest.update(json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
    manifest = {
        "schema_version": "human_keep_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dataset": "selection_v1",
        "n_labels": len(labels_out),
        "n_keep": sum(1 for row in labels_out if row["keep"]),
        "n_drop": sum(1 for row in labels_out if not row["keep"]),
        "counts": dict(Counter(row["sample_type"] for row in labels_out)),
        "irr_n": len(queue_out),
        "irr_seed": seed,
        "irr_quotas": IRR_QUOTAS,
        "labels_sha256": digest.hexdigest(),
        "notes": (
            "keep = ordinary|highlight (deliverable), drop = technical_hard|semantic_defect. "
            "Derived from human sample_type, not Qwen overall and not highlight-only "
            "provenance.human_keep. labels_r2.jsonl is the second-rater file; hide scores."
        ),
    }
    _write_json(out_dir / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=ROOT / "data" / "eval" / "selection_v1",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=20260817)
    parser.add_argument("--irr-n", type=int, default=100)
    args = parser.parse_args(argv)
    if not (args.dataset / "frozen_manifest.json").is_file():
        print(f"error: missing {args.dataset / 'frozen_manifest.json'}", file=sys.stderr)
        return 2
    manifest = build(args.dataset, args.out, seed=args.seed, irr_n=args.irr_n)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["n_labels"] < 100:
        print("error: expected ≥100 human keep labels", file=sys.stderr)
        return 1
    if manifest["irr_n"] < 100:
        print("error: IRR queue shorter than 100", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
