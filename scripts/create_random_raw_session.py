#!/usr/bin/env python3
"""
Create a random RAW-only test session under Livehouse_Archive.

Example:
  python scripts/create_random_raw_session.py \
    --archive-root "$LUMA_ARCHIVE_ROOT" \
    --count 20
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
from datetime import datetime
from pathlib import Path


def collect_arw_files(archive_root: Path) -> list[Path]:
    files: list[Path] = []
    for p in archive_root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".arw":
            continue
        if "RAW" not in p.parts:
            continue
        files.append(p)
    return files


def build_dest_session_dir(archive_root: Path, session_name: str | None) -> Path:
    if session_name:
        return archive_root / session_name
    ts = datetime.now().strftime("%Y-%m-%d_sample_%H%M%S")
    return archive_root / ts


def main() -> None:
    parser = argparse.ArgumentParser(description="Create random RAW session for end-to-end trigger.")
    parser.add_argument(
        "--archive-root",
        default=None,
        help="Archive root (default: $LUMA_ARCHIVE_ROOT, else ../Livehouse_Archive if go.mod in cwd, else ~/Livehouse_Archive)",
    )
    parser.add_argument("--count", type=int, default=20, help="How many RAW files to sample")
    parser.add_argument(
        "--session-name",
        default=None,
        help="Optional destination session folder name under archive root",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducible sampling")
    parser.add_argument("--dry-run", action="store_true", help="Only print sampled files")
    parser.add_argument("--overwrite", action="store_true", help="Delete destination session if exists")
    args = parser.parse_args()

    def _default_archive() -> Path:
        env = os.environ.get("LUMA_ARCHIVE_ROOT", "").strip()
        if env:
            return Path(env).expanduser().resolve()
        cwd = Path.cwd()
        if (cwd / "go.mod").is_file():
            return (cwd.parent / "Livehouse_Archive").resolve()
        return (Path.home() / "Livehouse_Archive").expanduser().resolve()

    archive_root = Path(args.archive_root).expanduser().resolve() if args.archive_root else _default_archive()
    if not archive_root.exists() or not archive_root.is_dir():
        raise SystemExit(f"archive root not found: {archive_root}")

    if args.count <= 0:
        raise SystemExit("--count must be > 0")

    candidates = collect_arw_files(archive_root)
    if not candidates:
        raise SystemExit(f"no ARW files found under: {archive_root}")

    if args.seed is not None:
        random.seed(args.seed)

    n = min(args.count, len(candidates))
    chosen = random.sample(candidates, n)

    dest_session_dir = build_dest_session_dir(archive_root, args.session_name)
    dest_raw_dir = dest_session_dir / "RAW"
    dest_previews_dir = dest_session_dir / "Previews"

    if dest_session_dir.exists():
        if not args.overwrite:
            raise SystemExit(
                f"destination exists: {dest_session_dir}\n"
                "use --overwrite to recreate"
            )
        shutil.rmtree(dest_session_dir)

    print(f"archive_root: {archive_root}")
    print(f"selected: {n}/{len(candidates)}")
    print(f"destination: {dest_session_dir}")

    for src in chosen:
        print(f"  - {src}")

    if args.dry_run:
        print("dry-run enabled, no files copied.")
        return

    dest_raw_dir.mkdir(parents=True, exist_ok=True)
    dest_previews_dir.mkdir(parents=True, exist_ok=True)

    # Keep original filenames where possible; append numeric suffix on collision.
    for src in chosen:
        target = dest_raw_dir / src.name
        if target.exists():
            stem = target.stem
            ext = target.suffix
            idx = 1
            while True:
                alt = dest_raw_dir / f"{stem}_{idx}{ext}"
                if not alt.exists():
                    target = alt
                    break
                idx += 1
        shutil.copy2(src, target)

    print("done.")
    print(f"RAW ready: {dest_raw_dir}")
    print(f"Previews dir initialized: {dest_previews_dir}")


if __name__ == "__main__":
    main()
