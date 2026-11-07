#!/usr/bin/env python3
"""Convert images in a directory to JPEG while preserving subdirectories."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps
except ImportError:
    sys.exit("缺少 Pillow，请运行：pip install Pillow pillow-heif")

try:
    import pillow_heif  # type: ignore[import-not-found]

    pillow_heif.register_heif_opener()
except ImportError:
    pillow_heif = None


DEFAULT_SOURCE = Path("/Users/jiayu/Downloads/psq")
DEFAULT_OUTPUT = Path("/Users/jiayu/Downloads/psq_preview")
IMAGE_SUFFIXES = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量把目录中的图像转换为 JPEG")
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--quality", type=int, default=95, choices=range(1, 101))
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的 JPEG")
    return parser.parse_args()


def convert_one(source: Path, output: Path, quality: int) -> None:
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened)
        if image.mode in {"RGBA", "LA"} or (
            image.mode == "P" and "transparency" in image.info
        ):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, "white")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        else:
            image = image.convert("RGB")

        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, "JPEG", quality=quality, optimize=True)


def main() -> int:
    args = parse_args()
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()

    if not source.is_dir():
        print(f"源目录不存在：{source}", file=sys.stderr)
        return 2
    if output == source or source in output.parents:
        print("输出目录不能是源目录或其子目录。", file=sys.stderr)
        return 2

    images = sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if any(path.suffix.lower() in {".heic", ".heif"} for path in images) and pillow_heif is None:
        print("发现 HEIC/HEIF 图像，请先运行：pip install pillow-heif", file=sys.stderr)
        return 2

    converted = skipped = failed = 0
    for path in images:
        destination = (output / path.relative_to(source)).with_suffix(".jpg")
        if destination.exists() and not args.overwrite:
            skipped += 1
            continue
        try:
            convert_one(path, destination, args.quality)
            converted += 1
            print(f"[完成] {path.name} -> {destination}")
        except Exception as exc:
            failed += 1
            print(f"[失败] {path}: {exc}", file=sys.stderr)

    print(
        f"\n处理结束：转换 {converted}，跳过 {skipped}，失败 {failed}；"
        f"输出目录：{output}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
