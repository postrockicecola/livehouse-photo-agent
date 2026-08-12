#!/usr/bin/env python3
"""Materialize EXIF-normalized, manually rotated JPEGs from an orientation review."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize(
    manifest_path: Path,
    orientation_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    review = json.loads(orientation_path.read_text(encoding="utf-8"))
    items = manifest.get("items") or []
    decisions = review.get("items") or {}
    file_ids = [Path(str(item.get("file") or "")).name for item in items]
    missing = [
        file_id
        for file_id in file_ids
        if not decisions.get(file_id, {}).get("reviewed")
    ]
    if missing:
        raise ValueError(f"orientation review incomplete: {len(missing)} files")

    output_dir.mkdir(parents=True, exist_ok=True)
    expected = set(file_ids)
    for existing in output_dir.iterdir():
        if existing.is_file() and existing.name not in expected:
            existing.unlink()

    normalized: list[dict[str, Any]] = []
    for item, file_id in zip(items, file_ids):
        source = Path(str(item.get("source_path") or ""))
        if not source.is_file():
            raise FileNotFoundError(source)
        degrees = int(decisions[file_id].get("rotation_degrees") or 0)
        destination = output_dir / file_id
        with Image.open(source) as image:
            image = ImageOps.exif_transpose(image)
            if degrees:
                image = image.rotate(-degrees, expand=True)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(
                destination,
                "JPEG",
                quality=95,
                subsampling=0,
                optimize=True,
            )
        normalized.append(
            {
                "file": file_id,
                "source_path": str(source),
                "rotation_degrees": degrees,
                "sha256": _sha256(destination),
            }
        )
    result = {
        "schema_version": "orientation_materialization.v1",
        "source_manifest": str(manifest_path),
        "orientation_review": str(orientation_path),
        "count": len(normalized),
        "items": normalized,
    }
    (output_dir / "materialization.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--orientation-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = materialize(args.manifest, args.orientation_review, args.output)
    rotated = sum(item["rotation_degrees"] != 0 for item in result["items"])
    print(f"Materialized {result['count']} images ({rotated} manually rotated)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
