#!/usr/bin/env python3
"""Build the local cross-session photo manifest and optional CLIP vectors.

Run:
    python -m scripts.build_archive_photo_index --archive-root /path/to/Livehouse_Archive
    python -m scripts.build_archive_photo_index --archive-root /path/to/archive --embed-images
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.archive_photo_index import build_archive_photo_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--max-sessions", type=int, default=80)
    parser.add_argument(
        "--embed-images",
        action="store_true",
        help="Materialize CLIP image vectors; text-only manifest is the default.",
    )
    args = parser.parse_args()
    meta = build_archive_photo_index(
        args.archive_root,
        max_sessions=max(1, args.max_sessions),
        embed_images=bool(args.embed_images),
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
