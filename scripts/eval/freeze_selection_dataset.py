#!/usr/bin/env python3
"""Validate and freeze the reviewed selection-evaluation dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TARGET_COUNTS = {
    "technical_hard": 50,
    "semantic_defect": 50,
    "ordinary": 100,
    "highlight": 50,
}
VALID_ROTATIONS = {0, 90, 180, 270}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def freeze_dataset(dataset_dir: Path) -> dict[str, Any]:
    paths = {
        "source_manifest": dataset_dir / "manifest.json",
        "labels": dataset_dir / "labels.jsonl",
        "defects": dataset_dir / "defects.json",
        "acceptable_pool": dataset_dir / "acceptable_pool.json",
        "orientation_review": dataset_dir / "orientation_review.json",
    }
    for path in paths.values():
        if not path.is_file():
            raise FileNotFoundError(path)

    manifest = _read_json(paths["source_manifest"])
    labels = _read_jsonl(paths["labels"])
    defects = _read_json(paths["defects"])
    acceptable = _read_json(paths["acceptable_pool"])
    orientation = _read_json(paths["orientation_review"])
    items = manifest.get("items") or []
    reviews = orientation.get("items") or {}

    manifest_ids = [str(item.get("file") or "") for item in items]
    label_ids = [str(row.get("file") or "") for row in labels]
    if len(manifest_ids) != 250 or len(set(manifest_ids)) != 250:
        raise ValueError("manifest must contain 250 unique file ids")
    if set(label_ids) != set(manifest_ids) or len(label_ids) != 250:
        raise ValueError("labels and manifest file ids do not match")
    if set(reviews) != set(manifest_ids):
        raise ValueError("orientation review file ids do not match manifest")

    labels_by_id = {str(row["file"]): row for row in labels}
    counts = Counter(str(row.get("sample_type") or "") for row in labels)
    if dict(counts) != TARGET_COUNTS:
        raise ValueError(f"unexpected category counts: {dict(counts)}")

    expected_defects = {
        file_id
        for file_id, row in labels_by_id.items()
        if row["sample_type"] in {"technical_hard", "semantic_defect"}
    }
    expected_acceptable = {
        file_id
        for file_id, row in labels_by_id.items()
        if row["sample_type"] == "highlight"
    }
    if set(defects) != expected_defects:
        raise ValueError("defects.json does not match the two defect categories")
    if set(acceptable) != expected_acceptable or len(acceptable) != 50:
        raise ValueError("acceptable_pool.json does not match highlight labels")

    rotation_counts: Counter[int] = Counter()
    frozen_items: list[dict[str, Any]] = []
    for item in items:
        file_id = str(item["file"])
        review = reviews[file_id]
        if review.get("reviewed") is not True:
            raise ValueError(f"{file_id}: orientation is not reviewed")
        degrees = int(review.get("rotation_degrees") or 0)
        if degrees not in VALID_ROTATIONS:
            raise ValueError(f"{file_id}: invalid rotation {degrees}")
        source = Path(str(item["source_path"]))
        actual_sha = _sha256(source)
        if actual_sha != item.get("sha256"):
            raise ValueError(f"{file_id}: source SHA-256 changed")
        if item.get("sample_type") != labels_by_id[file_id].get("sample_type"):
            raise ValueError(f"{file_id}: manifest and label categories differ")
        rotation_counts[degrees] += 1
        frozen_items.append(
            {
                **item,
                "orientation_correction_degrees": degrees,
            }
        )

    frozen_manifest = {
        "schema_version": "selection_eval_frozen_manifest.v1",
        "dataset_version": dataset_dir.name,
        "orientation_policy": (
            "apply EXIF transpose, then rotate clockwise by "
            "orientation_correction_degrees"
        ),
        "items": frozen_items,
    }
    frozen_manifest_path = dataset_dir / "frozen_manifest.json"
    frozen_manifest_path.write_text(
        json.dumps(frozen_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    artifact_hashes = {
        name: _sha256(path)
        for name, path in paths.items()
    }
    artifact_hashes["frozen_manifest"] = _sha256(frozen_manifest_path)
    fingerprint = _canonical_sha256(artifact_hashes)
    freeze_path = dataset_dir / "freeze.json"
    if freeze_path.is_file():
        existing_freeze = _read_json(freeze_path)
        if existing_freeze.get("dataset_sha256") != fingerprint:
            raise ValueError(
                "dataset is already frozen with a different fingerprint"
            )
        return existing_freeze
    freeze_record = {
        "schema_version": "selection_eval_freeze.v1",
        "dataset_version": dataset_dir.name,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": fingerprint,
        "total": len(items),
        "counts": TARGET_COUNTS,
        "orientation": {
            "reviewed": len(reviews),
            "corrections": len(items) - rotation_counts[0],
            "rotation_counts": {
                str(degrees): rotation_counts[degrees]
                for degrees in sorted(VALID_ROTATIONS)
            },
        },
        "artifact_sha256": artifact_hashes,
    }
    freeze_path.write_text(
        json.dumps(freeze_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return freeze_record


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "dataset_dir",
        type=Path,
        nargs="?",
        default=root / "data/eval/selection_v1",
    )
    args = parser.parse_args()
    result = freeze_dataset(args.dataset_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
