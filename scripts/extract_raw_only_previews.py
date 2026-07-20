#!/usr/bin/env python3
"""Extract gallery previews for archive sessions that contain only a RAW/ folder.

Scans immediate sub-directories of the Livehouse archive root. When a session
has no ``Previews/`` and no other content besides optional dotfiles (e.g.
``.DS_Store``), embedded JPEG previews are extracted from each file under
``RAW/`` into ``Previews/<stem>.jpg`` via ``exiftool``. JPEG files already
present in ``RAW/`` are copied into ``Previews/``.

Safe by default: dry-run unless ``--apply``. Existing preview files are never
overwritten.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from organize_legacy_sessions import (  # noqa: E402
    JPEG_EXTS,
    PREVIEWS_DIRNAME,
    RAW_DIRNAMES,
    RAW_EXTS,
    _extract_preview_jpeg,
    is_skippable_dir,
    resolve_archive_root,
)

# Prefer $LUMA_ARCHIVE_ROOT or --archive-root; no machine-specific default in-repo.
DEFAULT_ARCHIVE = ""


@dataclass
class SessionPlan:
    session_dir: Path
    raw_dir: Path
    previews_dir: Path
    copies: list[tuple[Path, Path]] = field(default_factory=list)
    extracts: list[tuple[Path, Path]] = field(default_factory=list)
    skipped_existing: int = 0

    @property
    def has_work(self) -> bool:
        return bool(self.copies or self.extracts)


def _find_raw_dir(session_dir: Path) -> Path | None:
    """Return RAW path when the session only contains RAW (+ skippable entries)."""
    raw_dir: Path | None = None
    try:
        entries = list(session_dir.iterdir())
    except OSError:
        return None
    for ent in entries:
        name = ent.name
        if name in (".DS_Store",) or is_skippable_dir(name):
            continue
        if ent.is_dir() and name in RAW_DIRNAMES:
            if raw_dir is not None:
                return None
            raw_dir = ent
            continue
        return None
    return raw_dir


def _iter_raw_images(raw_dir: Path) -> list[Path]:
    out: list[Path] = []
    try:
        for ent in sorted(raw_dir.iterdir()):
            if not ent.is_file():
                continue
            ext = ent.suffix.lower()
            if ext in JPEG_EXTS or ext in RAW_EXTS:
                out.append(ent)
    except OSError:
        pass
    return out


def plan_session(session_dir: Path) -> SessionPlan | None:
    raw_dir = _find_raw_dir(session_dir)
    if raw_dir is None:
        return None
    previews_dir = session_dir / PREVIEWS_DIRNAME
    plan = SessionPlan(session_dir=session_dir, raw_dir=raw_dir, previews_dir=previews_dir)
    for src in _iter_raw_images(raw_dir):
        ext = src.suffix.lower()
        if ext in JPEG_EXTS:
            dst = previews_dir / src.name
        else:
            dst = previews_dir / f"{src.stem}.jpg"
        if dst.exists():
            plan.skipped_existing += 1
            continue
        if ext in JPEG_EXTS:
            plan.copies.append((src, dst))
        else:
            plan.extracts.append((src, dst))
    return plan


def _copy_jpeg(src: Path, dst: Path) -> bool:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def apply_plan(plan: SessionPlan) -> dict[str, int]:
    counts = {"copied": 0, "extracted": 0, "extract_failed": 0, "copy_failed": 0}

    def one_extract(pair: tuple[Path, Path]) -> str:
        src, dst = pair
        return "extracted" if _extract_preview_jpeg(src, dst) else "extract_failed"

    def one_copy(pair: tuple[Path, Path]) -> str:
        src, dst = pair
        return "copied" if _copy_jpeg(src, dst) else "copy_failed"

    plan.previews_dir.mkdir(parents=True, exist_ok=True)
    for src, dst in plan.copies:
        key = one_copy((src, dst))
        counts[key] += 1
    for src, dst in plan.extracts:
        key = one_extract((src, dst))
        counts[key] += 1
    return counts


def apply_plan_parallel(plan: SessionPlan, workers: int) -> dict[str, int]:
    counts = {"copied": 0, "extracted": 0, "extract_failed": 0, "copy_failed": 0}
    plan.previews_dir.mkdir(parents=True, exist_ok=True)
    tasks: list[tuple[str, Path, Path]] = []
    for src, dst in plan.copies:
        tasks.append(("copy", src, dst))
    for src, dst in plan.extracts:
        tasks.append(("extract", src, dst))
    if not tasks:
        return counts

    def run(task: tuple[str, Path, Path]) -> tuple[str, bool]:
        kind, src, dst = task
        if kind == "copy":
            return ("copied", _copy_jpeg(src, dst))
        ok = _extract_preview_jpeg(src, dst)
        return ("extracted" if ok else "extract_failed", ok)

    w = max(1, workers)
    with ThreadPoolExecutor(max_workers=w) as pool:
        futures = [pool.submit(run, t) for t in tasks]
        for fut in as_completed(futures):
            key, ok = fut.result()
            if key == "copied" and not ok:
                counts["copy_failed"] += 1
            elif key == "extracted" and not ok:
                counts["extract_failed"] += 1
            else:
                counts[key] += 1
    return counts


def iter_session_dirs(archive_root: Path) -> list[Path]:
    out: list[Path] = []
    for ent in sorted(archive_root.iterdir()):
        if ent.is_dir() and not is_skippable_dir(ent.name):
            out.append(ent)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Extract Previews/ for sessions that only contain a RAW/ folder.",
    )
    ap.add_argument(
        "archive_root",
        nargs="?",
        default=None,
        help="Archive root (default: $LUMA_ARCHIVE_ROOT; required if unset).",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write Previews/ files. Without this flag, only prints the plan.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=max(2, (os.cpu_count() or 4) // 2),
        help="Parallel workers when using --apply (default: half of CPU count).",
    )
    args = ap.parse_args(argv)

    archive_arg = (args.archive_root or os.environ.get("LUMA_ARCHIVE_ROOT") or DEFAULT_ARCHIVE).strip()
    if not archive_arg:
        print("error: pass --archive-root or set LUMA_ARCHIVE_ROOT", file=sys.stderr)
        return 2
    archive_root = resolve_archive_root(archive_arg)
    if shutil.which("exiftool") is None:
        print("warning: exiftool not found on PATH; RAW extraction will fail.\n")

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"archive root : {archive_root}")
    print(f"mode         : {mode}")
    print(f"workers      : {args.workers}\n")

    matched = 0
    totals = {"copied": 0, "extract": 0, "skip_existing": 0}

    for sd in iter_session_dirs(archive_root):
        plan = plan_session(sd)
        if plan is None:
            continue
        matched += 1
        rel = sd.name
        print(f"• {rel}  (only RAW/)")
        print(f"    RAW files      : {len(plan.copies) + len(plan.extracts) + plan.skipped_existing}")
        if plan.copies:
            print(f"    JPEG copy      : {len(plan.copies)}")
        if plan.extracts:
            print(f"    RAW extract    : {len(plan.extracts)}")
        if plan.skipped_existing:
            print(f"    already in Previews: {plan.skipped_existing}")
        if not plan.has_work:
            print("    nothing to do")
            continue

        totals["copied"] += len(plan.copies)
        totals["extract"] += len(plan.extracts)
        totals["skip_existing"] += plan.skipped_existing

        if args.apply:
            if args.workers > 1 and (len(plan.copies) + len(plan.extracts)) > 1:
                counts = apply_plan_parallel(plan, args.workers)
            else:
                counts = apply_plan(plan)
            print(
                f"    done: copied={counts['copied']} extracted={counts['extracted']}"
                f" failed={counts['extract_failed'] + counts['copy_failed']}"
            )

    print("\n— summary —")
    print(f"sessions (RAW-only): {matched}")
    print(f"JPEG copies planned : {totals['copied']}")
    print(f"RAW extracts planned: {totals['extract']}")
    print(f"skipped (exists)  : {totals['skip_existing']}")
    if not args.apply and (totals["copied"] or totals["extract"]):
        print("\nDry-run only. Re-run with --apply to extract previews.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
