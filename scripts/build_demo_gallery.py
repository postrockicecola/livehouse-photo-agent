#!/usr/bin/env python3
"""
Build the bundled demo gallery for the landing page.

Takes a folder of your own photos, produces web-resolution, EXIF-stripped JPEGs
under web/public/demo/, and writes a manifest the landing API falls back to when
no live archive is reachable (fresh clone / Vercel deploy).

Privacy: EXIF (GPS, camera serial, timestamps, copyright/artist) is dropped, and
orientation is baked in before stripping. Only commit photos you are OK to publish.

Example:
  python scripts/build_demo_gallery.py --src ~/Pictures/livehouse_selects --count 12
  python scripts/build_demo_gallery.py --src ./selects --shuffle --seed 7
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("Pillow is required: pip install Pillow")

try:
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:
    pass  # HEIC support optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "web" / "public" / "demo"
DEFAULT_MANIFEST = REPO_ROOT / "web" / "lib" / "demoGallery.json"
EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}


def collect_sources(src: Path) -> list[Path]:
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in EXTS]
    return files


def verify_clean(path: Path) -> list[str]:
    """Return a list of leak reasons; empty list means metadata is clean."""
    issues: list[str] = []
    with Image.open(path) as im:
        if len(im.getexif()) > 0:
            issues.append("EXIF present")
        if "icc_profile" in im.info:
            issues.append("ICC profile present")
        if "exif" in im.info:
            issues.append("raw EXIF block present")
    return issues


def process_one(src_path: Path, dst_path: Path, max_side: int, quality: int) -> None:
    with Image.open(src_path) as im:
        im = ImageOps.exif_transpose(im)  # bake rotation, then we drop all metadata
        im = im.convert("RGB")  # new image without EXIF/ICC carried over
        w, h = im.size
        scale = min(1.0, max_side / float(max(w, h)))
        if scale < 1.0:
            im = im.resize((round(w * scale), round(h * scale)), Image.Resampling.LANCZOS)
        # No exif=/icc_profile= passed → metadata stripped.
        im.save(dst_path, format="JPEG", quality=quality, optimize=True, progressive=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build EXIF-stripped demo gallery for the landing page.")
    parser.add_argument("--src", required=True, type=Path, help="Folder of source photos (searched recursively).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"Output dir (default: {DEFAULT_OUT}).")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help=f"Manifest JSON (default: {DEFAULT_MANIFEST}).")
    parser.add_argument("--count", type=int, default=12, help="Max number of images (default: 12).")
    parser.add_argument("--max-side", type=int, default=2000, help="Long-edge cap in px (default: 2000).")
    parser.add_argument("--quality", type=int, default=80, help="JPEG quality (default: 80).")
    parser.add_argument("--shuffle", action="store_true", help="Randomly sample instead of taking the first N (sorted).")
    parser.add_argument("--seed", type=int, default=None, help="Random seed when --shuffle is set.")
    args = parser.parse_args()

    src: Path = args.src.expanduser()
    if not src.is_dir():
        sys.exit(f"--src is not a directory: {src}")

    sources = collect_sources(src)
    if not sources:
        sys.exit(f"No images found under {src} (looked for: {', '.join(sorted(EXTS))})")

    if args.shuffle:
        import random

        random.seed(args.seed)
        random.shuffle(sources)
    chosen = sources[: max(0, args.count)]

    out: Path = args.out.expanduser()
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("demo-*.jpg"):  # clean previous run
        old.unlink()

    images: list[dict[str, str]] = []
    for idx, src_path in enumerate(chosen, start=1):
        name = f"demo-{idx:02d}.jpg"
        dst_path = out / name
        try:
            process_one(src_path, dst_path, args.max_side, args.quality)
        except Exception as exc:  # skip unreadable files, keep going
            print(f"  skip {src_path.name}: {exc}")
            continue
        images.append({"path": f"/demo/{name}"})
        print(f"  + {name}  <-  {src_path.name}")

    if not images:
        sys.exit("No images were processed successfully.")

    # Self-check: confirm every output is free of EXIF/ICC before we trust it.
    leaks = False
    for entry in images:
        name = entry["path"].rsplit("/", 1)[-1]
        issues = verify_clean(out / name)
        if issues:
            leaks = True
            print(f"  ! {name}: {', '.join(issues)}")
    if leaks:
        sys.exit("VERIFY FAIL — some outputs still carry metadata (see above). Not writing manifest.")
    print(f"VERIFY PASS — {len(images)} images carry no EXIF/ICC metadata.")

    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "images": images}
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nDone: {len(images)} images -> {out}")
    print(f"Manifest: {args.manifest}")
    print("Review the images, then commit web/public/demo/ and web/lib/demoGallery.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
