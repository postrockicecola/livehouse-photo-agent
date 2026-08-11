#!/usr/bin/env python3
"""Build the local ACL-aware knowledge RAG index."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from services.knowledge_index import build_knowledge_index


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-dir", type=Path, required=True)
    parser.add_argument(
        "--embed-text",
        action="store_true",
        help="Materialize optional CLIP text vectors in addition to BM25.",
    )
    args = parser.parse_args()
    meta = build_knowledge_index(args.knowledge_dir, embed_text=bool(args.embed_text))
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
