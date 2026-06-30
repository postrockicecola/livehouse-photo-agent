#!/usr/bin/env python3
"""Relocate stale ``sessions`` path columns in the brain DB after an archive move.

When the Livehouse archive is moved/renamed, ``sessions.previews_dir`` (and friends) keep
pointing at the old location, which surfaces empty duplicate cards in the Studio list. This
one-off tool finds sessions whose ``previews_dir`` no longer exists on disk and re-points
them to ``<archive_root>/<session_key>/Previews`` under a *live* archive root.

Archive roots are discovered from (in order): ``--archive-root`` args, ``$LUMA_ARCHIVE_ROOT``,
and the parents of every still-valid ``previews_dir`` already in the table. Sessions with no
matching folder under any root are left untouched and only reported.

Dry-run by default; pass ``--apply`` to write.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# Allow running directly from the repo root (``python scripts/relocate_brain_sessions.py``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _default_db() -> str:
    try:
        from utils.luma_brain import brain_db_path

        return str(brain_db_path())
    except Exception:
        return "luma_brain.db"


def _candidate_roots(conn: sqlite3.Connection, cli_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add(p: str | Path | None) -> None:
        if not p:
            return
        try:
            r = Path(str(p)).expanduser().resolve()
        except OSError:
            return
        if r.is_dir() and str(r) not in seen:
            seen.add(str(r))
            roots.append(r)

    for r in cli_roots:
        add(r)
    add(os.environ.get("LUMA_ARCHIVE_ROOT"))
    for row in conn.execute("SELECT previews_dir FROM sessions"):
        pd = str(row[0] or "").strip()
        if pd and os.path.isdir(pd):
            # ``.../<root>/<session_key>/Previews`` → root is two levels up.
            add(Path(pd).parent.parent)
    return roots


def _find_relocation(session_key: str, roots: list[Path]) -> Path | None:
    for root in roots:
        cand = root / session_key / "Previews"
        if cand.is_dir():
            return cand.resolve()
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=_default_db(), help="Path to luma_brain.db")
    ap.add_argument(
        "--archive-root",
        action="append",
        default=[],
        help="Extra archive root to search (repeatable).",
    )
    ap.add_argument("--apply", action="store_true", help="Persist changes (default: dry-run).")
    args = ap.parse_args()

    db_path = str(Path(args.db).expanduser().resolve())
    if not os.path.isfile(db_path):
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    roots = _candidate_roots(conn, args.archive_root)
    print(f"DB: {db_path}")
    print("Archive roots searched:")
    for r in roots:
        print(f"  - {r}")
    if not roots:
        print("No live archive roots found; nothing can be relocated.", file=sys.stderr)
        return 1

    rows = conn.execute(
        "SELECT id, session_key, session_dir, archive_root, raw_dir, previews_dir FROM sessions"
    ).fetchall()

    relocated = 0
    orphaned: list[str] = []
    updates: list[tuple[int, dict[str, str]]] = []

    for r in rows:
        pd = str(r["previews_dir"] or "").strip()
        if pd and os.path.isdir(pd):
            continue  # already live
        session_key = str(r["session_key"]).strip()
        target = _find_relocation(session_key, roots)
        if target is None:
            orphaned.append(f"id={r['id']} {session_key} (old: {pd or '—'})")
            continue
        session_dir = target.parent
        archive_root = session_dir.parent
        new = {
            "previews_dir": str(target),
            "session_dir": str(session_dir),
            "archive_root": str(archive_root),
        }
        raw_cand = session_dir / "RAW"
        if raw_cand.is_dir():
            new["raw_dir"] = str(raw_cand.resolve())
        updates.append((int(r["id"]), new))
        relocated += 1
        print(f"\nid={r['id']} {session_key}")
        print(f"  previews_dir: {pd or '—'}")
        print(f"             -> {new['previews_dir']}")

    if args.apply and updates:
        for sid, new in updates:
            cols = ", ".join(f"{k} = ?" for k in new)
            conn.execute(f"UPDATE sessions SET {cols} WHERE id = ?", (*new.values(), sid))
        conn.commit()

    print("\n" + "=" * 48)
    print(f"Relocatable sessions: {relocated}")
    print(f"Orphaned (no folder found, left as-is): {len(orphaned)}")
    for o in orphaned:
        print(f"  - {o}")
    print("APPLIED changes to DB." if (args.apply and updates) else "DRY-RUN — pass --apply to write.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
