#!/usr/bin/env python3
"""Prepare candidate images and a manifest for ``relabel_qwen.py``."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_candidates(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if not isinstance(row, dict) or not row.get("file") or not row.get("source_path"):
                raise ValueError(f"{path}:{line_number}: missing file or source_path")
            rows.append(row)
    return rows


def prepare_relabel_inputs(
    candidates_path: Path,
    images_dir: Path,
    manifest_path: Path,
) -> int:
    """Create stable-name symlinks and a manifest accepted by relabel_qwen."""
    rows = load_candidates(candidates_path)
    images_dir.mkdir(parents=True, exist_ok=True)
    expected: set[str] = set()
    items: list[dict[str, Any]] = []
    for row in rows:
        file_id = Path(str(row["file"])).name
        source = Path(str(row["source_path"])).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"candidate source image missing: {source}")
        if file_id.casefold() in expected:
            raise ValueError(f"duplicate candidate filename: {file_id}")
        expected.add(file_id.casefold())
        link = images_dir / file_id
        if link.is_symlink() and link.resolve() != source:
            link.unlink()
        elif link.exists() and not link.is_symlink():
            raise FileExistsError(f"refusing to replace non-symlink: {link}")
        if not link.exists():
            link.symlink_to(source)
        items.append(
            {
                "file": file_id,
                "source_path": str(source),
                "session": row.get("session"),
                "target_category": row.get("target_category"),
            }
        )

    for existing in images_dir.iterdir():
        if existing.is_symlink() and existing.name.casefold() not in expected:
            existing.unlink()

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "candidate_relabel_manifest.v1",
                "source_candidates": str(candidates_path),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return len(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("--images-dir", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    base = args.candidates.parent
    images_dir = args.images_dir or base / "relabel_images"
    manifest = args.manifest or base / "relabel_manifest.json"
    count = prepare_relabel_inputs(args.candidates, images_dir, manifest)
    print(f"Prepared {count} relabel inputs")
    print(f"Images:   {images_dir}")
    print(f"Manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
