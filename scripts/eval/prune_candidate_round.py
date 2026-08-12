#!/usr/bin/env python3
"""Remove rejected files consistently from all artifacts in a candidate round."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


JSONL_ARTIFACTS = (
    "candidates.jsonl",
    "qwen_suggestions.jsonl",
    "human_reviews.jsonl",
    "review_log.jsonl",
    "provisional_triage.jsonl",
)


def _file_key(row: dict[str, Any]) -> str:
    return str(row.get("file") or "").casefold()


def prune_jsonl(path: Path, excluded: set[str]) -> tuple[int, int]:
    if not path.is_file():
        return 0, 0
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            kept.append(line)
            continue
        if isinstance(row, dict) and _file_key(row) in excluded:
            removed += 1
        else:
            kept.append(line)
    path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return len(kept), removed


def prune_round(round_dir: Path, excluded_files: list[str]) -> dict[str, Any]:
    excluded = {Path(file_id).name.casefold() for file_id in excluded_files if file_id}
    report: dict[str, Any] = {"excluded_files": sorted(excluded), "artifacts": {}}
    for name in JSONL_ARTIFACTS:
        kept, removed = prune_jsonl(round_dir / name, excluded)
        report["artifacts"][name] = {"kept": kept, "removed": removed}

    summary_path = round_dir / "candidates.summary.json"
    candidates_path = round_dir / "candidates.jsonl"
    if summary_path.is_file() and candidates_path.is_file():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        candidates = [
            json.loads(line)
            for line in candidates_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        summary["selected_counts"] = dict(
            Counter(str(row.get("target_category") or "unknown") for row in candidates)
        )
        summary["selected_total"] = len(candidates)
        summary["manually_excluded"] = len(excluded)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    blind_path = round_dir / "blind_split.json"
    if blind_path.is_file():
        blind = json.loads(blind_path.read_text(encoding="utf-8"))
        files = [
            file_id
            for file_id in blind.get("files") or []
            if Path(str(file_id)).name.casefold() not in excluded
        ]
        blind["files"] = files
        blind["n"] = len(files)
        blind_path.write_text(
            json.dumps(blind, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["blind_split"] = len(files)

    ledger_path = round_dir / "excluded_files.txt"
    existing = (
        {
            line.strip()
            for line in ledger_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        if ledger_path.is_file()
        else set()
    )
    ledger_path.write_text(
        "\n".join(sorted(existing | set(excluded_files), key=str.casefold)) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round_dir", type=Path)
    parser.add_argument("--exclude-list", type=Path, required=True)
    args = parser.parse_args()
    excluded_files = [
        line.strip()
        for line in args.exclude_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    report = prune_round(args.round_dir, excluded_files)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
