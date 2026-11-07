#!/usr/bin/env python3
"""Safely rename JPEG files to a sequential naming scheme."""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path


DEFAULT_DIRECTORY = Path(
    "/Volumes/M4Buffer/Visions/Livehouse_Archive/artifact/上吾其"
)
JPEG_SUFFIXES = {".jpg", ".jpeg"}


def natural_sort_key(path: Path) -> list[tuple[int, object]]:
    return [
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", path.stem)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按自然顺序将目录中的 JPEG 重命名为指定前缀的连续编号"
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help=f"图片目录（默认：{DEFAULT_DIRECTORY}）",
    )
    parser.add_argument(
        "--prefix",
        default="shangwuqi",
        help="目标文件名前缀（默认：shangwuqi）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="实际执行重命名；不加此参数时只预览",
    )
    parser.add_argument(
        "--sort",
        choices=("name", "modified"),
        default="name",
        help="排序方式：自然文件名顺序（默认）或修改时间顺序",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        print(f"目录不存在：{directory}", file=sys.stderr)
        return 2

    images = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in JPEG_SUFFIXES
    ]
    images.sort(
        key=(
            (lambda path: (path.stat().st_mtime_ns, natural_sort_key(path)))
            if args.sort == "modified"
            else natural_sort_key
        )
    )
    if not images:
        print(f"目录中没有 JPEG 图片：{directory}", file=sys.stderr)
        return 2

    width = max(2, len(str(len(images))))
    rename_pairs = [
        (source, directory / f"{args.prefix}_{index:0{width}d}.jpg")
        for index, source in enumerate(images, start=1)
    ]

    sources = set(images)
    conflicts = [
        destination
        for source, destination in rename_pairs
        if destination.exists() and destination not in sources and destination != source
    ]
    if conflicts:
        print("目标文件名已被其他文件占用：", file=sys.stderr)
        for path in conflicts:
            print(f"  {path.name}", file=sys.stderr)
        return 1

    for source, destination in rename_pairs:
        print(f"{source.name} -> {destination.name}")

    if not args.apply:
        print(f"\n预览完成，共 {len(rename_pairs)} 张；加 --apply 后实际执行。")
        return 0

    temporary_pairs: list[tuple[Path, Path]] = []
    token = uuid.uuid4().hex
    try:
        for index, (source, destination) in enumerate(rename_pairs, start=1):
            temporary = directory / f".rename-{token}-{index}.tmp"
            source.rename(temporary)
            temporary_pairs.append((temporary, destination))

        for temporary, destination in temporary_pairs:
            temporary.rename(destination)
    except OSError as exc:
        print(f"重命名失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n完成：已重命名 {len(rename_pairs)} 张图片，图片内容未修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
