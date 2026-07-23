#!/usr/bin/env python3
"""Export prompt-experiment + eval-manifest snapshot for Infra Experiments / interviews.

Usage:
  python scripts/eval/export_experiment_report.py
  python scripts/eval/export_experiment_report.py --out reports/eval/experiment_loop_latest.json

Does not invent quality metrics. Empty DB → honest empty arrays + provenance recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# Never commit host-local absolute paths. Keep basename when present.
_ABS_PATH_RE = re.compile(
    r"^(?:/Volumes/|/Users/|/Visions/)[^\n]*$"
)


def _redact_abs_path(value: str) -> str:
    if not _ABS_PATH_RE.match(value):
        return value
    name = Path(value).name
    return f"/archive/redacted/{name}" if name else "/archive/redacted"


def _scrub_paths(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _scrub_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_paths(v) for v in obj]
    if isinstance(obj, str):
        return _redact_abs_path(obj)
    return obj


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return row is not None


def _fetch_prompt_runs(conn: sqlite3.Connection, *, limit: int = 200) -> list[dict[str, Any]]:
    if not _table_exists(conn, "prompt_experiment_runs"):
        return []
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(prompt_experiment_runs)").fetchall()}
    # Be resilient to schema drift.
    select_cols = [c for c in (
        "id",
        "model_run_id",
        "variant_id",
        "experiment_name",
        "image_path",
        "vlm_score",
        "outcome",
        "prompt_tokens",
        "completion_tokens",
        "latency_ms",
        "created_at",
    ) if c in cols]
    if not select_cols:
        return []
    sql = f"SELECT {', '.join(select_cols)} FROM prompt_experiment_runs ORDER BY id DESC LIMIT ?"
    rows = conn.execute(sql, (limit,)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({k: r[k] for k in r.keys()})
    return out


def build_report(*, limit: int = 200) -> dict[str, Any]:
    from utils.luma_brain import brain_connect, brain_db_path

    manifest = _REPO / "data" / "eval" / "manifest.json"
    labels = _REPO / "data" / "eval" / "labels.jsonl"
    conn = brain_connect()
    try:
        runs = _fetch_prompt_runs(conn, limit=limit)
        variants: list[dict[str, Any]] = []
        if _table_exists(conn, "prompt_variants"):
            vcols = {str(r[1]) for r in conn.execute("PRAGMA table_info(prompt_variants)").fetchall()}
            want = [c for c in ("id", "name", "variant_tag", "active", "created_at") if c in vcols]
            if want:
                for r in conn.execute(
                    f"SELECT {', '.join(want)} FROM prompt_variants ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall():
                    variants.append({k: r[k] for k in r.keys()})
    finally:
        conn.close()

    db_path = Path(brain_db_path())
    try:
        brain_db_public = str(db_path.resolve().relative_to(_REPO.resolve()))
    except ValueError:
        brain_db_public = db_path.name

    report = {
        "schema_version": "1",
        "purpose": "Batch E experiment-loop snapshot (prompt runs + eval set digests)",
        "provenance": "recorded",
        "real_run": True,
        "generated_at": int(time.time()),
        "brain_db": brain_db_public,
        "eval_set": {
            "manifest_path": str(manifest.relative_to(_REPO)) if manifest.is_file() else None,
            "manifest_sha256": _sha256_file(manifest),
            "labels_path": str(labels.relative_to(_REPO)) if labels.is_file() else None,
            "labels_sha256": _sha256_file(labels),
        },
        "prompt_variants": variants,
        "prompt_experiment_runs": runs,
        "notes": [
            "Empty arrays mean no experiment rows in the local SSOT — not simulated metrics.",
            "Promote/release gates for prompts/models are still out of scope.",
            "Absolute host paths in image_path / brain_db are redacted for safe commits.",
        ],
    }
    return _scrub_paths(report)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--out",
        type=Path,
        default=_REPO / "reports" / "eval" / "experiment_loop_latest.json",
    )
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()
    report = build_report(limit=max(1, int(args.limit)))
    out: Path = args.out if args.out.is_absolute() else (_REPO / args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} (runs={len(report['prompt_experiment_runs'])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
