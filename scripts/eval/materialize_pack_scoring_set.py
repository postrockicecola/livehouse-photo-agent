#!/usr/bin/env python3
"""Materialize only pack images missing from an existing prediction cache."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


def materialize_missing(
    *,
    pack_manifest_path: Path,
    existing_predictions_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest = _read_json(pack_manifest_path)
    packs = [pack for pack in manifest.get("packs") or [] if isinstance(pack, dict)]
    existing = {
        _file_id(row) for row in _prediction_rows(existing_predictions_path) if _file_id(row)
    }
    sources: dict[str, Path] = {}
    pack_ids: dict[str, list[str]] = {}
    for pack in packs:
        for file_id in pack["files"]:
            if file_id in existing:
                continue
            source = Path(pack["source_paths"][file_id])
            previous = sources.get(file_id)
            if previous is not None and previous.resolve() != source.resolve():
                raise ValueError(f"conflicting source paths for {file_id}")
            sources[file_id] = source
            pack_ids.setdefault(file_id, []).append(str(pack["id"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for file_id, source in sorted(sources.items()):
        if not source.is_file():
            raise FileNotFoundError(source)
        destination = output_dir / file_id
        shutil.copy2(source, destination)
        items.append(
            {
                "file": file_id,
                "source_path": str(source),
                "materialized_path": str(destination),
                "pack_ids": sorted(set(pack_ids[file_id])),
            }
        )
    result = {
        "schema_version": "pack_missing_scoring_set.v1",
        "source_pack_manifest": str(pack_manifest_path),
        "existing_predictions": str(existing_predictions_path),
        "image_count": len(items),
        "items": items,
    }
    (output_dir / "scoring_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--existing-predictions", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = materialize_missing(
        pack_manifest_path=args.packs,
        existing_predictions_path=args.existing_predictions,
        output_dir=args.output_dir,
    )
    print(
        json.dumps(
            {"image_count": result["image_count"], "output_dir": str(args.output_dir)},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
