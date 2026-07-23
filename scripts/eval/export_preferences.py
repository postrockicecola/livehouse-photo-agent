#!/usr/bin/env python3
"""Minimal human-preference flywheel exporter (offline).

Turns keep/reject labels (and optional gallery curation decisions) into
pairwise preference records suitable for later SFT / DPO / reward training.

Does not train a model — only materializes a reproducible preference dataset
next to the eval labels so the data loop exists before the training loop.

Example::

    python scripts/eval/export_preferences.py \\
      --labels data/eval/labels.jsonl \\
      --out data/eval/preferences/pairs.jsonl
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval.protocol import stamp_protocol  # noqa: E402


def load_labeled(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("keep") is None and r.get("overall") is None:
            continue
        rows.append(r)
    return rows


def load_gallery_decisions(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    for key in ("decisions", "items", "photos", "selected"):
        if isinstance(data.get(key), list):
            return [r for r in data[key] if isinstance(r, dict)]
    return []


def build_pairs_from_labels(
    rows: list[dict[str, Any]],
    *,
    max_pairs: int,
    seed: int,
) -> list[dict[str, Any]]:
    """chosen = keep (or higher overall); rejected = non-keep (or lower overall)."""
    keeps = [r for r in rows if r.get("keep") is True]
    rejects = [r for r in rows if r.get("keep") is False]
    rng = random.Random(seed)
    pairs: list[dict[str, Any]] = []

    if keeps and rejects:
        for i in range(min(max_pairs, len(keeps) * 2)):
            c = keeps[i % len(keeps)]
            r = rejects[rng.randrange(len(rejects))]
            pairs.append(
                {
                    "pair_id": f"keep_reject_{i:04d}",
                    "source": "labels_keep_reject",
                    "chosen": {"file": c["file"], "overall": c.get("overall"), "keep": True},
                    "rejected": {"file": r["file"], "overall": r.get("overall"), "keep": False},
                }
            )
        return pairs[:max_pairs]

    # Fallback: overall-score ranking pairs when keep flags are sparse.
    scored = [r for r in rows if r.get("overall") is not None]
    scored.sort(key=lambda x: float(x["overall"]), reverse=True)
    if len(scored) < 2:
        return []
    for i in range(min(max_pairs, len(scored) - 1)):
        c, r = scored[i], scored[-(i % (len(scored) // 2) + 1)]
        if c["file"] == r["file"]:
            continue
        pairs.append(
            {
                "pair_id": f"overall_rank_{i:04d}",
                "source": "labels_overall_rank",
                "chosen": {"file": c["file"], "overall": c.get("overall"), "keep": c.get("keep")},
                "rejected": {"file": r["file"], "overall": r.get("overall"), "keep": r.get("keep")},
            }
        )
    return pairs[:max_pairs]


def build_pairs_from_gallery(
    decisions: list[dict[str, Any]],
    *,
    max_pairs: int,
) -> list[dict[str, Any]]:
    chosen = []
    rejected = []
    for r in decisions:
        fn = r.get("file") or r.get("file_name") or r.get("image_id")
        if not fn:
            continue
        status = str(r.get("status") or r.get("decision") or r.get("verdict") or "").lower()
        keep = r.get("keep")
        if keep is True or status in {"keep", "selected", "yes", "accepted"}:
            chosen.append(str(fn))
        elif keep is False or status in {"reject", "trash", "no", "discarded"}:
            rejected.append(str(fn))
    pairs: list[dict[str, Any]] = []
    for i, c in enumerate(chosen):
        if i >= max_pairs or not rejected:
            break
        r = rejected[i % len(rejected)]
        pairs.append(
            {
                "pair_id": f"gallery_{i:04d}",
                "source": "gallery_curation",
                "chosen": {"file": c},
                "rejected": {"file": r},
            }
        )
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--labels", default="data/eval/labels.jsonl")
    ap.add_argument(
        "--gallery",
        default=None,
        help="Optional gallery curation JSON (keep/reject decisions)",
    )
    ap.add_argument("--out", default="data/eval/preferences/pairs.jsonl")
    ap.add_argument("--manifest-out", default="data/eval/preferences/manifest.json")
    ap.add_argument("--max-pairs", type=int, default=400)
    ap.add_argument("--seed", type=int, default=20260625)
    args = ap.parse_args()

    labels = load_labeled(Path(args.labels))
    gallery = load_gallery_decisions(Path(args.gallery) if args.gallery else None)

    pairs = build_pairs_from_labels(labels, max_pairs=args.max_pairs, seed=args.seed)
    if gallery:
        pairs.extend(build_pairs_from_gallery(gallery, max_pairs=max(0, args.max_pairs // 4)))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for p in pairs:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")

    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n_pairs": len(pairs),
        "sources": sorted({p["source"] for p in pairs}),
        "labels_path": args.labels,
        "gallery_path": args.gallery,
        "pairs_path": str(out.as_posix()),
        "purpose": "Offline preference pairs for future SFT/DPO/reward — not a trained model.",
    }
    stamp_protocol(
        manifest,
        labels_path=args.labels,
        seed=args.seed,
        extra={"max_pairs": args.max_pairs},
    )
    man_path = Path(args.manifest_out)
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(pairs)} pairs → {out}")
    print(f"manifest → {man_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
