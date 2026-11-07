#!/usr/bin/env python3
"""Render a folder through the current ``film_default`` grade for visual review."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.film_render_service import (  # noqa: E402
    _apply_variant,
    load_rgb_u8,
    normalize_grade_luminance,
)

DEFAULT_INPUT = Path(
    "/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-09-09/"
    "exported_images/export_20260910_235122_695765/raw"
)
SUPPORTED_EXTENSIONS = frozenset(
    {".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf", ".jpg", ".jpeg", ".png"}
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Defaults to a timestamp-named sibling of input_dir.",
    )
    parser.add_argument(
        "--full-size",
        action="store_true",
        help="Decode RAW at full resolution; default half-size is faster for visual tests.",
    )
    parser.add_argument("--max-side", type=int, default=3000)
    return parser.parse_args()


def render_one(source: Path, destination: Path, *, full_size: bool, max_side: int) -> None:
    rgb = load_rgb_u8(
        source,
        raw_half_size=not full_size,
        preserve_raw_highlights=True,
    )
    graded = _apply_variant(rgb, "film_default")
    graded = normalize_grade_luminance(rgb, graded, "film_default")
    image = Image.fromarray(graded, mode="RGB")
    if max_side > 0:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    image.save(destination, format="JPEG", quality=95, subsampling=0, optimize=True)


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise SystemExit(f"input directory not found: {input_dir}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else input_dir.parent / timestamp
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    sources = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not sources:
        output_dir.rmdir()
        raise SystemExit(f"no supported images found in: {input_dir}")

    failures: list[tuple[Path, str]] = []
    for index, source in enumerate(sources, start=1):
        destination = output_dir / f"{source.stem}.jpg"
        try:
            render_one(
                source,
                destination,
                full_size=bool(args.full_size),
                max_side=max(0, int(args.max_side)),
            )
            print(f"[{index:02d}/{len(sources):02d}] {source.name} -> {destination.name}")
        except Exception as exc:
            failures.append((source, str(exc)))
            print(f"[{index:02d}/{len(sources):02d}] FAILED {source.name}: {exc}", file=sys.stderr)

    print(f"output: {output_dir}")
    print(f"rendered: {len(sources) - len(failures)}, failed: {len(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
