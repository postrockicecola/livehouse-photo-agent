#!/usr/bin/env python3
"""Build deterministic real-session packs for reranker development."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


def build_pack_manifest(
    *,
    dataset_manifest_path: Path,
    predictions_path: Path,
    output_path: Path,
    min_size: int = 8,
    max_size: int = 15,
    seed: int = 20260811,
    holdout_fraction: float = 0.2,
) -> dict[str, Any]:
    dataset = _read_json(dataset_manifest_path)
    items = [row for row in dataset.get("items") or [] if isinstance(row, dict)]
    predictions = {
        _file_id(row): row for row in _prediction_rows(predictions_path) if _file_id(row)
    }
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        file_id = str(item.get("file") or "")
        if file_id and file_id in predictions:
            by_session[str(item.get("session") or "")].append(item)

    eligible_sessions = sorted(
        session for session, rows in by_session.items() if len(rows) >= min_size
    )
    holdout_count = max(1, round(len(eligible_sessions) * holdout_fraction))
    holdout_sessions = set(
        sorted(
            eligible_sessions,
            key=lambda session: hashlib.sha256(
                f"{seed}:{session}".encode("utf-8")
            ).hexdigest(),
        )[:holdout_count]
    )

    packs: list[dict[str, Any]] = []
    for index, session in enumerate(eligible_sessions, start=1):
        ranked = sorted(
            by_session[session],
            key=lambda item: (
                -float(predictions[str(item["file"])].get("overall_score") or 0),
                str(item["file"]),
            ),
        )[:max_size]
        packs.append(
            {
                "id": f"real_pack_{index:02d}",
                "session": session,
                "split": "holdout" if session in holdout_sessions else "development",
                "files": [str(item["file"]) for item in ranked],
                "source_paths": {
                    str(item["file"]): str(item.get("source_path") or "")
                    for item in ranked
                },
            }
        )

    result = {
        "schema_version": "pack_reranker_dev.v1",
        "seed": seed,
        "min_size": min_size,
        "max_size": max_size,
        "holdout_fraction": holdout_fraction,
        "dataset_manifest": str(dataset_manifest_path),
        "dataset_manifest_sha256": _sha256(dataset_manifest_path),
        "predictions": str(predictions_path),
        "predictions_sha256": _sha256(predictions_path),
        "pack_count": len(packs),
        "development_pack_count": sum(
            pack["split"] == "development" for pack in packs
        ),
        "holdout_pack_count": sum(pack["split"] == "holdout" for pack in packs),
        "image_count": sum(len(pack["files"]) for pack in packs),
        "packs": packs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--min-size", type=int, default=8)
    parser.add_argument("--max-size", type=int, default=15)
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--holdout-fraction", type=float, default=0.2)
    args = parser.parse_args()
    result = build_pack_manifest(
        dataset_manifest_path=args.manifest,
        predictions_path=args.predictions,
        output_path=args.output,
        min_size=max(5, args.min_size),
        max_size=max(args.min_size, args.max_size),
        seed=args.seed,
        holdout_fraction=max(0.05, min(0.5, args.holdout_fraction)),
    )
    print(
        json.dumps(
            {
                "packs": result["pack_count"],
                "development": result["development_pack_count"],
                "holdout": result["holdout_pack_count"],
                "images": result["image_count"],
                "output": str(args.output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
