#!/usr/bin/env python3
"""
Generate lightweight test sessions under Livehouse_Archive (or --archive-root).

Modes
-----
  sample       — Randomly copy real image files from --sample-source into Session/Previews/
                 (default source: $LUMA_ARCHIVE_ROOT, else ./Livehouse_Archive). Default --count 20.

  previews     — Session/Previews/*.png (valid grayscale PNG, stdlib only). Use with:
                 python run_pipeline.py --config configs/livehouse.yaml --source-dir <.../Previews> --no-serve

  raw-stubs    — Session/RAW/*.arw (empty files). Enough for Go ingest *discovery* / stable-window
                 counters; preview extraction + exiftool will fail on empty files — use for scanner-only
                 tests or pair with real ARW later.

  both         — Previews + RAW stubs (mixed session).

Examples
--------
  python scripts/generate_test_data.py --mode sample
  python scripts/generate_test_data.py --mode sample --count 20 --seed 42
  python scripts/generate_test_data.py --mode previews --count 12
  python scripts/generate_test_data.py --mode raw-stubs --count 5 --session-name 2026-04-14_smoke
  LUMA_ARCHIVE_ROOT=/tmp/lh python scripts/generate_test_data.py --mode previews
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import struct
import zlib
from datetime import datetime
from pathlib import Path

DEFAULT_SAMPLE_SOURCE = Path(os.environ.get("LUMA_ARCHIVE_ROOT", "Livehouse_Archive"))

SKIP_PATH_PARTS = frozenset(
    {
        ".runtime",
        "runtime",
        ".git",
        "node_modules",
        ".next",
        "__pycache__",
        "exported_images",
    }
)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def default_archive_root() -> Path:
    env = os.environ.get("LUMA_ARCHIVE_ROOT", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "go.mod").is_file():
        return (cwd.parent / "Livehouse_Archive").resolve()
    return (Path.home() / "Livehouse_Archive").expanduser().resolve()


def session_dir_name(explicit: str | None) -> str:
    if explicit:
        return explicit
    return datetime.now().strftime("%Y-%m-%d_smoke_%H%M%S")


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def write_gray_png(path: Path, width: int, height: int, gray: int) -> None:
    """Minimal valid grayscale PNG (stdlib only; no Pillow)."""
    gray = max(0, min(255, int(gray)))
    raw_rows = []
    for _ in range(height):
        raw_rows.append(bytes([0]) + bytes([gray]) * width)
    raw = b"".join(raw_rows)
    compressed = zlib.compress(raw, 9)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    body = sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", compressed) + _png_chunk(b"IEND", b"")
    path.write_bytes(body)


def write_preview_images(previews_dir: Path, count: int, prefix: str = "DSC") -> None:
    """Write small PNG previews (OpenCV / pipeline accept .png)."""
    previews_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        name = f"{prefix}{i:05d}.png"
        path = previews_dir / name
        write_gray_png(path, 128, 96, 40 + (i * 7) % 180)
        print(f"  wrote {path}")


def write_raw_stubs(raw_dir: Path, count: int, prefix: str = "DSC") -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        name = f"{prefix}{i:05d}.ARW"
        path = raw_dir / name
        path.write_bytes(b"")  # empty placeholder
        print(f"  wrote {path}")


def _path_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def collect_image_files(sample_root: Path, exclude_under: Path) -> list[Path]:
    """Recursive image paths under sample_root; skips junk dirs; excludes future session dir."""
    ex = exclude_under.resolve()
    out: list[Path] = []
    for p in sample_root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_PATH_PARTS for part in p.parts):
            continue
        if p.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        try:
            if _path_under(p.resolve(), ex):
                continue
        except OSError:
            continue
        out.append(p)
    return out


def copy_random_sample(
    sample_source: Path,
    previews_dir: Path,
    count: int,
    seed: int | None,
) -> None:
    previews_dir.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        random.seed(seed)

    candidates = collect_image_files(sample_source, previews_dir.parent)
    if not candidates:
        raise SystemExit(
            f"no image files (.jpg/.jpeg/.png/.webp) found under: {sample_source}\n"
            f"(excluding session output {previews_dir.parent})"
        )

    n = min(count, len(candidates))
    chosen = random.sample(candidates, n)
    used_names: set[str] = set()

    for i, src in enumerate(chosen, start=1):
        base = src.name
        if base in used_names:
            stem = src.stem
            ext = src.suffix
            base = f"{stem}_{i}{ext}"
            j = 1
            while base in used_names:
                base = f"{stem}_{i}_{j}{ext}"
                j += 1
        used_names.add(base)
        dst = previews_dir / base
        shutil.copy2(src, dst)
        print(f"  {src} -> {dst}")

    print(f"copied {n} file(s) (pool had {len(candidates)} images).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate test archive sessions (JPEG previews and/or empty .ARW stubs).",
    )
    parser.add_argument(
        "--archive-root",
        type=str,
        default=None,
        help="Override archive root (default: $LUMA_ARCHIVE_ROOT, else ../Livehouse_Archive from repo, else ~/Livehouse_Archive)",
    )
    parser.add_argument(
        "--mode",
        choices=("sample", "previews", "raw-stubs", "both"),
        default="sample",
        help="sample=random real images from --sample-source; previews=synthetic PNGs; raw-stubs=empty ARW",
    )
    parser.add_argument(
        "--sample-source",
        type=str,
        default=None,
        help=f"Root to scan for images when --mode sample (default: {DEFAULT_SAMPLE_SOURCE})",
    )
    parser.add_argument("--session-name", default=None, help="Folder name under archive (default: dated smoke name)")
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Number of files (default: 20 for sample, 8 for previews/raw-stubs/both)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for --mode sample (reproducible draws)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Remove existing session directory if it exists",
    )
    args = parser.parse_args()

    count_default = 20 if args.mode == "sample" else 8
    count = args.count if args.count is not None else count_default
    if count <= 0:
        raise SystemExit("--count must be > 0")

    sample_source = (
        Path(args.sample_source).expanduser().resolve()
        if args.sample_source
        else DEFAULT_SAMPLE_SOURCE
    )

    if args.mode == "sample":
        archive_root = Path(args.archive_root).expanduser().resolve() if args.archive_root else sample_source
    else:
        archive_root = Path(args.archive_root).expanduser().resolve() if args.archive_root else default_archive_root()

    archive_root.mkdir(parents=True, exist_ok=True)

    name = session_dir_name(args.session_name)
    dest = archive_root / name

    if dest.exists():
        if not args.overwrite:
            raise SystemExit(f"destination exists: {dest}\nUse --overwrite to replace.")
        shutil.rmtree(dest)

    dest.mkdir(parents=True)

    print(f"archive_root: {archive_root}")
    print(f"session:      {dest}")
    print(f"mode:         {args.mode}")

    if args.mode == "sample":
        if not sample_source.is_dir():
            raise SystemExit(f"sample source not found or not a directory: {sample_source}")
        print(f"sample_source: {sample_source}")
        copy_random_sample(sample_source, dest / "Previews", count, args.seed)
    elif args.mode in ("previews", "both"):
        write_preview_images(dest / "Previews", count)
    if args.mode in ("raw-stubs", "both"):
        write_raw_stubs(dest / "RAW", count)

    print("\nNext steps:")
    if args.mode in ("sample", "previews", "both"):
        prev = dest / "Previews"
        print(
            f"  python run_pipeline.py --config configs/livehouse.yaml "
            f'--source-dir "{prev}" --no-serve'
        )
    if args.mode in ("raw-stubs", "both"):
        print(
            "  Go ingest: empty .ARW files are for scanner/state tests only; "
            "preview extraction needs real RAW files."
        )
        print(
            f'  go run . --archive-root "{archive_root}" --only-session "{name}" '
            f"--poll-seconds 2 --stable-seconds 3 --verbose"
        )


if __name__ == "__main__":
    main()
