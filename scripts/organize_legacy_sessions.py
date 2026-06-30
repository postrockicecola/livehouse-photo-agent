#!/usr/bin/env python3
"""Reorganize legacy archive folders into valid Studio sessions.

A legacy shoot folder usually holds loose images directly in the session dir::

    Session/IMG_0001.JPG
    Session/IMG_0001.ARW
    Session/IMG_0002.JPG
    ...

Studio / gallery expect the canonical layout (see ``utils/studio_sessions.py``
``scan_archive_session_dirs`` + ``preview_extractor.go``)::

    Session/Previews/<stem>.jpg     # gallery previews (scan counts these)
    Session/RAW/<stem>.<rawext>     # paired RAW, located on export by stem

This script scans each immediate sub-directory of the archive root and moves
(or copies) the *loose top-level* JPEG files into ``Previews/`` and RAW files
into ``RAW/``. RAW files that have no sibling JPEG can optionally get an
embedded preview extracted via ``exiftool`` (``--extract-previews``).

Safe by default: it runs a dry-run and prints the plan. Pass ``--apply`` to
actually move/copy files. Existing destination files are never overwritten.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Source of truth for these sets:
#   RAW exts  -> preview_extractor.go / services/film_render_service.py
#   skip dirs -> utils/studio_sessions._SKIP_ARCHIVE_DIRS
RAW_EXTS = frozenset({".arw", ".dng", ".cr2", ".cr3", ".nef", ".raf", ".rw2", ".orf"})
JPEG_EXTS = frozenset({".jpg", ".jpeg"})
SKIP_DIRS = frozenset({".runtime", "runtime", ".git", ".DS_Store"})
RAW_DIRNAMES = ("RAW", "Raw", "raw")
PREVIEWS_DIRNAME = "Previews"


@dataclass
class FileMove:
    src: Path
    dst: Path


@dataclass
class SessionPlan:
    session_dir: Path
    jpeg_moves: list[FileMove] = field(default_factory=list)
    raw_moves: list[FileMove] = field(default_factory=list)
    raw_preview_extracts: list[FileMove] = field(default_factory=list)  # src=raw, dst=Previews/*.jpg
    conflicts: list[Path] = field(default_factory=list)
    other_files: list[Path] = field(default_factory=list)

    @property
    def has_work(self) -> bool:
        return bool(self.jpeg_moves or self.raw_moves or self.raw_preview_extracts)


def resolve_archive_root(arg: str | None) -> Path:
    raw = (arg or os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if not raw:
        sys.exit(
            "error: archive root not given. Pass it as an argument or set "
            "LUMA_ARCHIVE_ROOT, e.g.\n"
            "  python scripts/organize_legacy_sessions.py /path/to/Livehouse_Archive"
        )
    root = Path(raw).expanduser()
    if not root.is_dir():
        sys.exit(f"error: archive root is not a directory: {root}")
    return root.resolve()


def is_skippable_dir(name: str) -> bool:
    return name.startswith(".") or name in SKIP_DIRS


def iter_session_dirs(archive_root: Path) -> list[Path]:
    out: list[Path] = []
    for ent in sorted(archive_root.iterdir()):
        if not ent.is_dir():
            continue
        if is_skippable_dir(ent.name):
            continue
        out.append(ent)
    return out


def _existing_preview_stems(previews_dir: Path) -> set[str]:
    stems: set[str] = set()
    if not previews_dir.is_dir():
        return stems
    try:
        for ent in previews_dir.iterdir():
            if ent.is_file() and ent.suffix.lower() in JPEG_EXTS:
                stems.add(ent.stem.lower())
    except OSError:
        pass
    return stems


def _top_level_files(session_dir: Path) -> list[Path]:
    try:
        return [p for p in sorted(session_dir.iterdir()) if p.is_file()]
    except OSError:
        return []


# Canonical sub-dirs that already hold organized files; never descend into them.
_ORGANIZED_DIRNAMES = frozenset({PREVIEWS_DIRNAME, *RAW_DIRNAMES})


def _collect_image_files(session_dir: Path, *, recursive: bool) -> list[Path]:
    """Image files (JPEG + RAW) to relocate.

    Non-recursive: only loose files at the session root.
    Recursive: all images anywhere under the session, except inside the
    session's own ``Previews/`` / ``RAW`` dirs and hidden/skip dirs.
    """
    img_exts = JPEG_EXTS | RAW_EXTS
    if not recursive:
        return [p for p in _top_level_files(session_dir) if p.suffix.lower() in img_exts]

    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(session_dir):
        here = Path(dirpath)
        at_root = here == session_dir
        dirnames[:] = sorted(
            d
            for d in dirnames
            if not is_skippable_dir(d) and not (at_root and d in _ORGANIZED_DIRNAMES)
        )
        for name in sorted(filenames):
            p = here / name
            if p.suffix.lower() in img_exts:
                out.append(p)
    return sorted(out)


def _unique_dst(dst: Path, claimed: set[Path]) -> Path | None:
    """Return dst if free (on disk and within this run), else None (skip; never overwrite)."""
    if dst in claimed or dst.exists():
        return None
    return dst


def plan_session(session_dir: Path, *, extract_previews: bool, recursive: bool) -> SessionPlan:
    plan = SessionPlan(session_dir=session_dir)
    previews_dir = session_dir / PREVIEWS_DIRNAME
    raw_dir = session_dir / RAW_DIRNAMES[0]

    files = _collect_image_files(session_dir, recursive=recursive)
    # Non-image files left in place — reported from the session root only.
    plan.other_files = [
        p
        for p in _top_level_files(session_dir)
        if p.suffix.lower() not in (JPEG_EXTS | RAW_EXTS)
    ]
    if not files:
        return plan

    existing_preview_stems = _existing_preview_stems(previews_dir)
    # Track destinations claimed during planning so two sources can't collide.
    claimed_prev: set[Path] = set()
    claimed_raw: set[Path] = set()

    jpeg_stems_here: set[str] = set()
    raw_files: list[Path] = []

    for f in files:
        ext = f.suffix.lower()
        if ext in JPEG_EXTS:
            jpeg_stems_here.add(f.stem.lower())
            dst = _unique_dst(previews_dir / f"{f.stem}.jpg", claimed_prev)
            if dst is None:
                plan.conflicts.append(f)
                continue
            claimed_prev.add(dst)
            plan.jpeg_moves.append(FileMove(src=f, dst=dst))
        elif ext in RAW_EXTS:
            raw_files.append(f)

    for f in raw_files:
        dst = _unique_dst(raw_dir / f.name, claimed_raw)
        if dst is None:
            plan.conflicts.append(f)
            continue
        claimed_raw.add(dst)
        plan.raw_moves.append(FileMove(src=f, dst=dst))

        # RAW with no preview (neither a sibling JPEG here nor an existing Previews/<stem>.jpg)
        stem = f.stem.lower()
        has_preview = stem in jpeg_stems_here or stem in existing_preview_stems
        if not has_preview and extract_previews:
            prev_dst = _unique_dst(previews_dir / f"{f.stem}.jpg", claimed_prev)
            if prev_dst is not None:
                claimed_prev.add(prev_dst)
                plan.raw_preview_extracts.append(FileMove(src=f, dst=prev_dst))

    return plan


def _transfer(src: Path, dst: Path, *, copy: bool) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copy2(src, dst)
    else:
        shutil.move(str(src), str(dst))


def _extract_preview_jpeg(raw_src: Path, dst: Path) -> bool:
    """Extract an embedded preview from RAW via exiftool. Returns True on a non-empty write."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for tag in ("-PreviewImage", "-JpgFromRaw", "-ThumbnailImage"):
        try:
            with open(dst, "wb") as out:
                proc = subprocess.run(
                    ["exiftool", "-b", tag, str(raw_src)],
                    stdout=out,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
        except OSError:
            return False
        if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            return True
    if dst.exists() and dst.stat().st_size == 0:
        dst.unlink(missing_ok=True)
    return False


def apply_plan(plan: SessionPlan, *, copy: bool, extract_previews: bool) -> dict[str, int]:
    counts = {"jpeg": 0, "raw": 0, "extracted": 0, "extract_failed": 0}
    for mv in plan.jpeg_moves:
        _transfer(mv.src, mv.dst, copy=copy)
        counts["jpeg"] += 1
    # Extract previews BEFORE moving RAW so the source path is still valid.
    if extract_previews:
        for mv in plan.raw_preview_extracts:
            if _extract_preview_jpeg(mv.src, mv.dst):
                counts["extracted"] += 1
            else:
                counts["extract_failed"] += 1
    for mv in plan.raw_moves:
        _transfer(mv.src, mv.dst, copy=copy)
        counts["raw"] += 1
    return counts


def prune_empty_dirs(session_dir: Path) -> int:
    """Remove now-empty sub-directories under *session_dir* (bottom-up). Keeps the root."""
    removed = 0
    for dirpath, _dirnames, _filenames in os.walk(session_dir, topdown=False):
        here = Path(dirpath)
        if here == session_dir:
            continue
        try:
            next(here.iterdir())
        except StopIteration:
            try:
                here.rmdir()
                removed += 1
            except OSError:
                pass
        except OSError:
            pass
    return removed


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Reorganize legacy archive folders into valid Studio sessions "
        "(Previews/ + RAW/).",
    )
    ap.add_argument(
        "archive_root",
        nargs="?",
        default=None,
        help="Archive root. Defaults to $LUMA_ARCHIVE_ROOT.",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Actually move/copy files. Without it, only prints the plan (dry-run).",
    )
    ap.add_argument(
        "--copy",
        action="store_true",
        help="Copy instead of move (doubles disk usage; safer for a first pass).",
    )
    ap.add_argument(
        "--extract-previews",
        action="store_true",
        help="For RAW files without a sibling JPEG, extract an embedded preview "
        "via exiftool into Previews/<stem>.jpg.",
    )
    ap.add_argument(
        "--recursive",
        action="store_true",
        help="Also relocate images nested in sub-folders of each session "
        "(skips the session's own Previews/ and RAW/). Empty sub-folders are "
        "removed afterwards on --apply.",
    )
    args = ap.parse_args(argv)

    archive_root = resolve_archive_root(args.archive_root)
    extract_previews = args.extract_previews

    if extract_previews and shutil.which("exiftool") is None:
        print("warning: exiftool not found on PATH; --extract-previews will be skipped.\n")
        extract_previews = False

    mode = "APPLY" if args.apply else "DRY-RUN"
    transfer = "copy" if args.copy else "move"
    print(f"archive root : {archive_root}")
    print(f"mode         : {mode} ({transfer})")
    print(f"extract raw  : {'yes' if extract_previews else 'no'}")
    print(f"recursive    : {'yes' if args.recursive else 'no'}\n")

    session_dirs = iter_session_dirs(archive_root)
    if not session_dirs:
        print("No candidate sub-directories found.")
        return 0

    totals = {"jpeg": 0, "raw": 0, "extract": 0, "conflicts": 0}
    processed = 0
    skipped = 0
    raw_without_preview = 0

    pruned_dirs = 0
    for sd in session_dirs:
        plan = plan_session(sd, extract_previews=extract_previews, recursive=args.recursive)
        if not plan.has_work:
            # Either already organized, or no loose images at top level.
            skipped += 1
            continue

        processed += 1
        rel = _rel(sd, archive_root)
        print(f"• {rel}")
        if plan.jpeg_moves:
            print(f"    JPEG  -> Previews/ : {len(plan.jpeg_moves)}")
        if plan.raw_moves:
            print(f"    RAW   -> RAW/      : {len(plan.raw_moves)}")
        if plan.raw_preview_extracts:
            print(f"    RAW   -> preview   : {len(plan.raw_preview_extracts)} (exiftool)")
        if plan.conflicts:
            print(f"    SKIP (dest exists) : {len(plan.conflicts)}")
            for c in plan.conflicts:
                print(f"        ! {c.name}")
        if plan.other_files:
            print(f"    left in place      : {len(plan.other_files)} non-image file(s)")

        totals["jpeg"] += len(plan.jpeg_moves)
        totals["raw"] += len(plan.raw_moves)
        totals["extract"] += len(plan.raw_preview_extracts)
        totals["conflicts"] += len(plan.conflicts)

        # Count RAW that will have no preview even after this run (info only).
        if not extract_previews:
            prev_stems = _existing_preview_stems(sd / PREVIEWS_DIRNAME)
            jpeg_here = {m.dst.stem.lower() for m in plan.jpeg_moves}
            for mv in plan.raw_moves:
                if mv.src.stem.lower() not in prev_stems and mv.src.stem.lower() not in jpeg_here:
                    raw_without_preview += 1

        if args.apply:
            counts = apply_plan(plan, copy=args.copy, extract_previews=extract_previews)
            note = (
                f"    done: jpeg={counts['jpeg']} raw={counts['raw']} "
                f"extracted={counts['extracted']}"
            )
            if counts["extract_failed"]:
                note += f" extract_failed={counts['extract_failed']}"
            print(note)
            if args.recursive:
                pruned_dirs += prune_empty_dirs(sd)

    print("\n— summary —")
    print(f"sessions with work : {processed}")
    print(f"sessions skipped   : {skipped} (already organized / no loose images)")
    print(f"JPEG -> Previews   : {totals['jpeg']}")
    print(f"RAW  -> RAW        : {totals['raw']}")
    if extract_previews:
        print(f"RAW  -> preview    : {totals['extract']}")
    if totals["conflicts"]:
        print(f"conflicts skipped  : {totals['conflicts']}")
    if args.recursive and pruned_dirs:
        print(f"empty dirs removed : {pruned_dirs}")
    if not extract_previews and raw_without_preview:
        print(
            f"RAW without preview: {raw_without_preview} "
            "(re-run with --extract-previews to generate gallery previews for these)"
        )
    if not args.apply:
        print("\nDry-run only. Re-run with --apply to perform the changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
