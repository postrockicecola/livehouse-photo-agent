#!/usr/bin/env python3
"""Build a Stage3 eval image set from archived sessions.

Two sampling modes:

1. ``--archive-root``: discover every session under the archive and allocate the
   target **evenly per session** (small sessions contribute what they have; the
   shortfall is redistributed). Within a session, images are stratified by the
   session's Stage2 ``overall_score`` deciles when ``analysis_results.json``
   exists, otherwise sampled uniformly.
2. Repeated ``--session``: pool the given sessions and stratify by score decile
   over the pooled distribution (legacy v1 behavior).

Filenames are prefixed with the session name (``20260606__DSC05532.jpg``)
because DSC numbering repeats across sessions and the eval join key is the
basename.

Usage::

    python scripts/eval/sample_eval_set.py \
        --archive-root /Volumes/.../Livehouse_Archive \
        --exclude-session yewai --exclude-session smoke --exclude-session sample \
        --target 250 --out data/eval/images --manifest data/eval/manifest.json

The manifest (tracked in git) records source path, sha256, and the Stage2
score/category at sampling time; the copied images stay local-only.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import random
import re
import shutil
import sys
from pathlib import Path
from typing import Any

N_BUCKETS = 10
# Pipeline-generated folders under Previews/ that hold copies/exports, not originals.
SKIP_DIRS = {"graded_from_raw", "AI_Selected_Final", "manual_selected", "runtime", "Selected"}


def _session_tag(session_dir: Path) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", session_dir.name)[:24] or "session"


def _load_records(session_dir: Path) -> list[dict[str, Any]] | None:
    f = session_dir / "Previews" / "analysis_results.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    return [r for r in data if isinstance(r, dict)]


def _iter_preview_images(session_dir: Path) -> dict[str, Path]:
    """Unique basename -> path under Previews/, skipping export/copy folders."""
    previews = session_dir / "Previews"
    out: dict[str, Path] = {}
    if not previews.is_dir():
        return out
    for f in previews.rglob("*.jpg"):
        rel_parts = f.relative_to(previews).parts[:-1]
        if any(p in SKIP_DIRS for p in rel_parts):
            continue
        out.setdefault(f.name, f)
    return out


def _resolve_image(session_dir: Path, rec: dict[str, Any]) -> Path | None:
    p = rec.get("path")
    if p:
        cand = Path(str(p))
        if cand.is_file():
            return cand
    name = rec.get("file")
    if not name:
        return None
    previews = session_dir / "Previews"
    direct = previews / str(name)
    if direct.is_file():
        return direct
    hits = list(previews.rglob(str(name)))
    return hits[0] if hits else None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _stratified_pick(
    items: list[dict[str, Any]],
    quota: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    """Pick ``quota`` items spread over score deciles (items need 'score' or are uniform)."""
    scored = [i for i in items if isinstance(i.get("score"), (int, float))]
    unscored = [i for i in items if not isinstance(i.get("score"), (int, float))]
    if not scored:
        pool = list(items)
        rng.shuffle(pool)
        return pool[:quota]

    values = sorted(float(i["score"]) for i in scored)

    def bucket(v: float) -> int:
        rank = bisect.bisect_left(values, v)
        return min(N_BUCKETS - 1, rank * N_BUCKETS // len(values))

    buckets: dict[int, list[dict[str, Any]]] = {i: [] for i in range(N_BUCKETS)}
    for it in scored:
        buckets[bucket(float(it["score"]))].append(it)
    for b in buckets.values():
        rng.shuffle(b)

    picked: list[dict[str, Any]] = []
    # round-robin over buckets so every decile is represented before repeats
    idx = {i: 0 for i in range(N_BUCKETS)}
    while len(picked) < quota:
        advanced = False
        for i in range(N_BUCKETS):
            if len(picked) >= quota:
                break
            if idx[i] < len(buckets[i]):
                picked.append(buckets[i][idx[i]])
                idx[i] += 1
                advanced = True
        if not advanced:
            break
    if len(picked) < quota and unscored:
        rng.shuffle(unscored)
        picked.extend(unscored[: quota - len(picked)])
    return picked


def _session_items(session_dir: Path) -> list[dict[str, Any]]:
    """All sampleable images of a session: {'name','path','score','category'}."""
    recs = _load_records(session_dir)
    images = _iter_preview_images(session_dir)
    items: list[dict[str, Any]] = []
    if recs:
        seen: set[str] = set()
        for rec in recs:
            name = Path(str(rec.get("file") or "")).name
            if not name or name in seen:
                continue
            src = _resolve_image(session_dir, rec)
            if src is None:
                continue
            seen.add(name)
            items.append(
                {
                    "name": name,
                    "path": src,
                    "score": rec.get("overall_score") if isinstance(rec.get("overall_score"), (int, float)) else None,
                    "category": rec.get("category"),
                }
            )
        # previews that never made it into results (e.g. added later) — keep them samplable
        for name, path in images.items():
            if name not in seen:
                items.append({"name": name, "path": path, "score": None, "category": None})
    else:
        items = [{"name": n, "path": p, "score": None, "category": None} for n, p in images.items()]
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-root", help="discover all sessions under this root (even per-session allocation)")
    ap.add_argument("--session", action="append", default=[], help="explicit session dir; repeatable (pooled mode)")
    ap.add_argument(
        "--exclude-session",
        action="append",
        default=[],
        help="substring/regex matched against session folder name; repeatable",
    )
    ap.add_argument("--target", type=int, default=250)
    ap.add_argument("--out", default="data/eval/images")
    ap.add_argument("--manifest", default="data/eval/manifest.json")
    ap.add_argument("--seed", type=int, default=20260611)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    excludes = [re.compile(p) for p in args.exclude_session]

    def excluded(name: str) -> bool:
        return any(p.search(name) for p in excludes)

    sessions: list[Path] = []
    if args.archive_root:
        root = Path(args.archive_root).expanduser()
        for s in sorted(p for p in root.iterdir() if p.is_dir()):
            if s.name.startswith(".") or excluded(s.name):
                continue
            if (s / "Previews").is_dir():
                sessions.append(s)
    for s in args.session:
        sd = Path(s).expanduser()
        if not excluded(sd.name):
            sessions.append(sd)
    if not sessions:
        raise SystemExit("no sessions to sample from")

    per_session_items = {s: _session_items(s) for s in sessions}
    per_session_items = {s: it for s, it in per_session_items.items() if it}
    sessions = list(per_session_items.keys())
    print(f"sessions with sampleable previews: {len(sessions)}")

    # Even allocation with shortfall redistribution (small sessions give what they have).
    chosen: list[tuple[Path, dict[str, Any]]] = []
    if args.archive_root:
        remaining_target = args.target
        remaining = {s: list(per_session_items[s]) for s in sessions}
        active = [s for s in sessions if remaining[s]]
        quotas: dict[Path, int] = {s: 0 for s in sessions}
        while remaining_target > 0 and active:
            share = max(1, remaining_target // len(active))
            progressed = False
            for s in list(active):
                if remaining_target <= 0:
                    break
                take = min(share, len(remaining[s]) - quotas[s], remaining_target)
                if take > 0:
                    quotas[s] += take
                    remaining_target -= take
                    progressed = True
                if quotas[s] >= len(remaining[s]):
                    active.remove(s)
            if not progressed:
                break
        for s in sessions:
            if quotas[s] > 0:
                for it in _stratified_pick(per_session_items[s], quotas[s], rng):
                    chosen.append((s, it))
    else:
        pooled = [(s, it) for s in sessions for it in per_session_items[s]]
        scored_pool = [{"name": it["name"], "path": it["path"], "score": it["score"], "category": it["category"], "_s": s} for s, it in pooled]
        for it in _stratified_pick(scored_pool, args.target, rng):
            chosen.append((it["_s"], it))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for sdir, it in chosen:
        new_name = f"{_session_tag(sdir)}__{it['name']}"
        if new_name.lower() in seen_names:
            continue
        seen_names.add(new_name.lower())
        dst = out_dir / new_name
        if not dst.exists():
            shutil.copy2(it["path"], dst)
        manifest.append(
            {
                "file": new_name,
                "source_path": str(it["path"]),
                "session": sdir.name,
                "sha256": _sha256(dst),
                "stage2_overall_score": it.get("score"),
                "stage2_category": it.get("category"),
            }
        )

    mpath = Path(args.manifest)
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(
        json.dumps({"seed": args.seed, "target": args.target, "items": manifest}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    by_session: dict[str, int] = {}
    for m in manifest:
        by_session[m["session"]] = by_session.get(m["session"], 0) + 1
    print(f"copied {len(manifest)} images to {out_dir}")
    for k in sorted(by_session):
        print(f"  {k:32s} {by_session[k]}")
    print(f"manifest: {mpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
