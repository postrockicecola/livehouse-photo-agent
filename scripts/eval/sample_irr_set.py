#!/usr/bin/env python3
"""Sample a stratified subset for inter-rater / re-label reliability (IRR).

Pulls 30–50 images from the 250-label golden set, copies them into a blind
folder (no prior scores, no AI reference), and seals round-1 labels for later
agreement scoring.

Usage::

    python scripts/eval/sample_irr_set.py \\
        --labels data/eval/labels.jsonl \\
        --images data/eval/images \\
        --n 40 --seed 20260725 \\
        --out data/eval/irr_round2

Then blind-label (no --predictions)::

    python scripts/label_server.py \\
        --images data/eval/irr_round2/images \\
        --labels data/eval/irr_round2/labels_r2.jsonl \\
        --predictions ''

After labeling, score agreement::

    python scripts/eval/eval_inter_rater.py \\
        --r1 data/eval/irr_round2/labels_r1_sealed.jsonl \\
        --r2 data/eval/irr_round2/labels_r2.jsonl \\
        --out reports/eval/inter_rater_round2.json
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.eval.labels import load_labels, normalize_name


def _band(overall: float) -> str:
    """Score bands. Extreme is oversampled — only ~11/250 sit below 50."""
    if overall < 50:
        return "extreme"
    if overall < 70:
        return "low"
    if overall < 80:
        return "mid"
    return "high"


# Default quotas for n=40: oversample extremes so IRR covers the trash/keep edge.
_DEFAULT_BAND_QUOTAS = {"extreme": 6, "low": 8, "mid": 14, "high": 12}


def _band_quotas(n: int) -> dict[str, int]:
    base = dict(_DEFAULT_BAND_QUOTAS)
    base_n = sum(base.values())
    if n == base_n:
        return base
    # Scale proportionally for other n, keep ≥1 on extreme when n≥10.
    scaled = {k: max(0, int(round(v / base_n * n))) for k, v in base.items()}
    if n >= 10:
        scaled["extreme"] = max(scaled["extreme"], min(6, n // 6))
    while sum(scaled.values()) < n:
        # Prefer mid/high (where model/human disagree most).
        for k in ("mid", "high", "low", "extreme"):
            scaled[k] += 1
            if sum(scaled.values()) >= n:
                break
    while sum(scaled.values()) > n:
        for k in ("mid", "high", "low", "extreme"):
            if scaled[k] > (1 if k == "extreme" and n >= 10 else 0):
                scaled[k] -= 1
                if sum(scaled.values()) <= n:
                    break
    return scaled


def sample_files(
    labels_path: Path,
    *,
    n: int,
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    labels = [lb for lb in load_labels(labels_path) if lb.overall is not None]
    by_band: dict[str, list] = defaultdict(list)
    for lb in labels:
        by_band[_band(float(lb.overall))].append(lb)

    counts = {k: len(v) for k, v in by_band.items()}
    quotas = _band_quotas(n)
    rng = random.Random(seed)

    picked = []
    allocated: dict[str, int] = {}
    for band, want in quotas.items():
        pool = list(by_band.get(band, []))
        # Within band: shuffle, but soft-balance keep/drop by alternating after sort key.
        rng.shuffle(pool)
        pool.sort(key=lambda lb: (0 if lb.keep is True else 1 if lb.keep is False else 2, rng.random()))
        # Re-shuffle lightly inside keep groups so we don't always take the same files.
        keepers = [lb for lb in pool if lb.keep is True]
        drops = [lb for lb in pool if lb.keep is not True]
        rng.shuffle(keepers)
        rng.shuffle(drops)
        # Interleave to avoid all-keeps in high band
        mixed: list = []
        i = j = 0
        take_keep = True
        while len(mixed) < len(pool):
            if take_keep and i < len(keepers):
                mixed.append(keepers[i])
                i += 1
            elif j < len(drops):
                mixed.append(drops[j])
                j += 1
            elif i < len(keepers):
                mixed.append(keepers[i])
                i += 1
            take_keep = not take_keep

        take = min(want, len(mixed))
        allocated[band] = take
        for lb in mixed[:take]:
            picked.append(
                {
                    "file": Path(lb.file).name,
                    "stratum": band,
                    "r1_overall": lb.overall,
                    "r1_keep": lb.keep,
                }
            )

    # Top up from remaining mid/high if a band was short
    shortfall = n - len(picked)
    if shortfall > 0:
        picked_keys = {normalize_name(r["file"]) for r in picked}
        leftovers = [lb for lb in labels if normalize_name(lb.file) not in picked_keys]
        # Prefer bands where model-human mid-band disagreement lives
        leftovers.sort(key=lambda lb: ({"mid": 0, "high": 1, "low": 2, "extreme": 3}[_band(float(lb.overall))], rng.random()))
        for lb in leftovers[:shortfall]:
            band = _band(float(lb.overall))
            allocated[band] = allocated.get(band, 0) + 1
            picked.append(
                {
                    "file": Path(lb.file).name,
                    "stratum": band,
                    "r1_overall": lb.overall,
                    "r1_keep": lb.keep,
                }
            )

    # Blind presentation order
    order = list(picked)
    rng.shuffle(order)
    for i, row in enumerate(order):
        row["blind_order"] = i

    meta = {
        "seed": seed,
        "requested_n": n,
        "sampled_n": len(picked),
        "source_labels": str(labels_path),
        "strata_available": counts,
        "strata_allocated": allocated,
        "protocol": {
            "blind": True,
            "no_ai_reference": True,
            "recommended_gap": "re-label yourself after ≥2 days, or use a second rater",
            "primary_metrics": ["spearman_overall", "icc2_1", "icc3_1", "keep_cohen_kappa"],
            "interpretation": {
                "human_spearman_0.5_0.6": "GT noise-dominated; model 0.36 less damning",
                "human_spearman_0.8_plus": "GT stable; 0.36 is model/pipeline ceiling",
            },
        },
    }
    return order, meta


def materialize(
    sample: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    images_dir: Path,
    labels_path: Path,
    out_dir: Path,
) -> None:
    img_out = out_dir / "images"
    img_out.mkdir(parents=True, exist_ok=True)

    # Index source images by normalized key and basename
    index: dict[str, Path] = {}
    for p in images_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            index.setdefault(normalize_name(p.name), p)
            index.setdefault(p.name.lower(), p)

    sealed: list[dict[str, Any]] = []
    raw_by_key = {}
    with open(labels_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            rec = json.loads(line)
            f = rec.get("file") or rec.get("path")
            if f:
                raw_by_key[normalize_name(str(f))] = rec

    missing: list[str] = []
    for row in sorted(sample, key=lambda r: r["blind_order"]):
        name = row["file"]
        src = index.get(normalize_name(name)) or index.get(name.lower())
        if src is None:
            missing.append(name)
            continue
        dst = img_out / name
        if not dst.exists():
            shutil.copy2(src, dst)
        sealed.append(raw_by_key[normalize_name(name)])

    if missing:
        raise FileNotFoundError(f"missing {len(missing)} images under {images_dir}: {missing[:5]}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sample_manifest.json").write_text(
        json.dumps({"meta": meta, "items": sample}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    with open(out_dir / "labels_r1_sealed.jsonl", "w", encoding="utf-8") as fh:
        for rec in sealed:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    r2 = out_dir / "labels_r2.jsonl"
    if not r2.exists():
        r2.write_text("", encoding="utf-8")

    # Human-facing README (short) — rater should not open sealed file
    readme = out_dir / "README_BLIND.txt"
    readme.write_text(
        "\n".join(
            [
                "IRR blind re-label set",
                "======================",
                f"n={meta['sampled_n']}  seed={meta['seed']}",
                "",
                "DO NOT open labels_r1_sealed.jsonl or sample_manifest.json while labeling.",
                "Those hold round-1 scores (answer key).",
                "",
                "Label command (AI reference OFF):",
                f"  python scripts/label_server.py --images {img_out} --labels {r2} --predictions ''",
                "",
                "Score agreement after finishing:",
                "  python scripts/eval/eval_inter_rater.py \\",
                f"      --r1 {out_dir / 'labels_r1_sealed.jsonl'} \\",
                f"      --r2 {r2} \\",
                "      --out reports/eval/inter_rater_round2.json",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--labels", default="data/eval/labels.jsonl")
    ap.add_argument("--images", default="data/eval/images")
    ap.add_argument("--out", default="data/eval/irr_round2")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20260725)
    args = ap.parse_args(argv)

    labels_path = Path(args.labels)
    images_dir = Path(args.images)
    out_dir = Path(args.out)
    if not labels_path.is_file():
        print(f"error: labels not found: {labels_path}", file=sys.stderr)
        return 2
    if not images_dir.is_dir():
        print(f"error: images dir not found: {images_dir}", file=sys.stderr)
        return 2

    sample, meta = sample_files(labels_path, n=args.n, seed=args.seed)
    materialize(sample, meta, images_dir=images_dir, labels_path=labels_path, out_dir=out_dir)

    print(f"sampled {meta['sampled_n']} → {out_dir}")
    print("strata_allocated:", json.dumps(meta["strata_allocated"], sort_keys=True))
    print(f"blind label: python scripts/label_server.py --images {out_dir / 'images'} "
          f"--labels {out_dir / 'labels_r2.jsonl'} --predictions ''")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
