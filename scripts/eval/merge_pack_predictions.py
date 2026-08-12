#!/usr/bin/env python3
"""Merge prediction caches and retain exactly the files referenced by pack manifests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


def merge_pack_predictions(
    *,
    pack_manifest_path: Path,
    prediction_paths: list[Path],
    output_path: Path,
) -> list[dict[str, Any]]:
    manifest = _read_json(pack_manifest_path)
    required = {
        str(file_id)
        for pack in manifest.get("packs") or []
        for file_id in pack.get("files") or []
    }
    merged: dict[str, dict[str, Any]] = {}
    for path in prediction_paths:
        for row in _rows(path):
            file_id = _file_id(row)
            if file_id in required:
                merged[file_id] = row
    missing = sorted(required - set(merged))
    if missing:
        raise ValueError(f"missing predictions: {missing[:10]}")
    result = [merged[file_id] for file_id in sorted(required)]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = merge_pack_predictions(
        pack_manifest_path=args.packs,
        prediction_paths=args.predictions,
        output_path=args.output,
    )
    print(json.dumps({"merged": len(result), "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
