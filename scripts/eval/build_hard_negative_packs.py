#!/usr/bin/env python3
"""Build same-session packs enriched with known defects and burst neighbors."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.operators.stage2_prefilter import hamming_64, image_phash_int

_FRAME_RE = re.compile(r"(\d+)(?!.*\d)")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


def _score(row: dict[str, Any]) -> float:
    return float(row.get("overall_score") or (row.get("scores") or {}).get("overall") or 0)


def _item_score(
    item: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
) -> float:
    if item.get("_selection_score") is not None:
        return float(item["_selection_score"])
    return _score(predictions[item["file"]])


def _frame_number(name: str) -> int | None:
    match = _FRAME_RE.search(Path(name).stem)
    return int(match.group(1)) if match else None


def _phash(path: Path, cache: dict[Path, int]) -> int:
    if path not in cache:
        image = cv2.imread(str(path))
        cache[path] = image_phash_int(image) if image is not None else 0
    return cache[path]


def _burst_neighbor(
    seed: dict[str, Any],
    *,
    all_dataset_sources: set[Path],
    used_sources: set[Path],
    phash_cache: dict[Path, int],
    max_frame_gap: int,
    max_hamming: int,
) -> tuple[Path, int] | None:
    source = Path(seed["source_path"])
    seed_number = _frame_number(source.name)
    if seed_number is None or not source.parent.is_dir():
        return None
    seed_hash = _phash(source, phash_cache)
    candidates = []
    for candidate in source.parent.iterdir():
        if candidate.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        resolved = candidate.resolve()
        if resolved in all_dataset_sources or resolved in used_sources:
            continue
        number = _frame_number(candidate.name)
        if number is None:
            continue
        frame_gap = abs(number - seed_number)
        if not 1 <= frame_gap <= max_frame_gap:
            continue
        candidate_hash = _phash(candidate, phash_cache)
        if not seed_hash or not candidate_hash:
            continue
        distance = hamming_64(seed_hash, candidate_hash)
        candidates.append((distance, frame_gap, candidate))
    if not candidates:
        return None
    distance, _, candidate = min(candidates)
    if distance > max_hamming:
        return None
    return candidate, distance


def _archive_candidates(
    session_rows: list[dict[str, Any]],
    *,
    existing_basenames: set[str],
) -> list[dict[str, Any]]:
    if not session_rows:
        return []
    source_parent = Path(session_rows[0]["source_path"]).parent
    results_path = source_parent / "analysis_results.json"
    if not results_path.is_file():
        return []
    prefix = str(session_rows[0]["file"]).split("__", 1)[0]
    current_paths = {
        path.name: path
        for path in source_parent.rglob("*")
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    }
    candidates = []
    for row in _prediction_rows(results_path):
        predicted_path = Path(str(row.get("path") or ""))
        basename = Path(str(row.get("file") or predicted_path.name)).name
        source = predicted_path if predicted_path.is_file() else current_paths.get(basename)
        if (
            source is None
            or basename in existing_basenames
            or _score(row) < 70
            or "low_quality" in (row.get("tags") or [])
        ):
            continue
        candidates.append(
            {
                "file": f"{prefix}__{basename}",
                "session": session_rows[0]["session"],
                "source_path": str(source),
                "_selection_score": _score(row),
                "_archive_candidate": True,
            }
        )
    candidates.sort(key=lambda item: (-float(item["_selection_score"]), item["file"]))
    return candidates


def build_hard_negative_manifest(
    *,
    frozen_manifest_path: Path,
    defects_path: Path,
    acceptable_path: Path,
    predictions_path: Path,
    output_path: Path,
    max_size: int = 15,
    base_size: int = 13,
    duplicate_candidates: int = 2,
    max_frame_gap: int = 25,
    max_hamming: int = 18,
    seed: int = 20260812,
) -> dict[str, Any]:
    frozen = _read_json(frozen_manifest_path)
    items = [row for row in frozen.get("items") or [] if isinstance(row, dict)]
    defects = _read_json(defects_path)
    acceptable = set(_read_json(acceptable_path))
    predictions = {
        _file_id(row): row for row in _prediction_rows(predictions_path) if _file_id(row)
    }
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("file") in predictions:
            by_session[str(item.get("session") or "")].append(item)

    eligible = sorted(
        session
        for session, rows in by_session.items()
        if len(rows) >= 8
        and any(row["file"] in defects for row in rows)
    )
    holdout_count = max(2, round(len(eligible) * 0.25))
    holdout_sessions = set(
        sorted(
            eligible,
            key=lambda session: hashlib.sha256(
                f"{seed}:{session}".encode("utf-8")
            ).hexdigest(),
        )[:holdout_count]
    )
    all_dataset_sources = {
        Path(str(item["source_path"])).resolve() for item in items if item.get("source_path")
    }
    phash_cache: dict[Path, int] = {}
    packs = []
    for index, session in enumerate(eligible, start=1):
        rows = by_session[session]
        archive_rows = _archive_candidates(
            rows,
            existing_basenames={Path(str(row["source_path"])).name for row in rows},
        )
        defect_rows = sorted(
            (row for row in rows if row["file"] in defects),
            key=lambda row: (-_item_score(row, predictions), row["file"]),
        )
        reviewed_acceptable_rows = sorted(
            (row for row in rows if row["file"] in acceptable),
            key=lambda row: (-_item_score(row, predictions), row["file"]),
        )
        good_candidate_rows = reviewed_acceptable_rows + [
            row
            for row in archive_rows
            if row["file"] not in {item["file"] for item in reviewed_acceptable_rows}
        ]
        selected: list[dict[str, Any]] = []
        for row in defect_rows[:5] + good_candidate_rows[:6]:
            if row not in selected:
                selected.append(row)
        for row in sorted(
            rows + archive_rows,
            key=lambda item: (-_item_score(item, predictions), item["file"]),
        ):
            if len(selected) >= base_size:
                break
            if row not in selected:
                selected.append(row)

        source_paths = {
            str(row["file"]): str(row["source_path"]) for row in selected[:base_size]
        }
        burst_seeds: dict[str, str] = {}
        burst_hamming: dict[str, int] = {}
        used_sources: set[Path] = set()
        for seed_row in good_candidate_rows:
            if len(burst_seeds) >= duplicate_candidates or len(source_paths) >= max_size:
                break
            found = _burst_neighbor(
                seed_row,
                all_dataset_sources=all_dataset_sources,
                used_sources=used_sources,
                phash_cache=phash_cache,
                max_frame_gap=max_frame_gap,
                max_hamming=max_hamming,
            )
            if found is None:
                continue
            neighbor, distance = found
            prefix = str(seed_row["file"]).split("__", 1)[0]
            file_id = f"{prefix}__{neighbor.name}"
            if file_id in source_paths:
                continue
            source_paths[file_id] = str(neighbor)
            used_sources.add(neighbor.resolve())
            burst_seeds[file_id] = str(seed_row["file"])
            burst_hamming[file_id] = distance

        files = list(source_paths)
        packs.append(
            {
                "id": f"hard_pack_{index:02d}",
                "session": session,
                "split": "holdout" if session in holdout_sessions else "development",
                "files": files,
                "source_paths": source_paths,
                "known_defect_ids": [file_id for file_id in files if file_id in defects],
                "known_defect_reasons": {
                    file_id: defects[file_id].get("reasons") or []
                    for file_id in files
                    if file_id in defects
                },
                "unreviewed_archive_ids": [
                    file_id
                    for file_id in files
                    if any(
                        row["file"] == file_id and row.get("_archive_candidate")
                        for row in archive_rows
                    )
                ],
                "burst_duplicate_seed": burst_seeds,
                "burst_duplicate_hamming": burst_hamming,
            }
        )

    result = {
        "schema_version": "pack_hard_negative.v1",
        "seed": seed,
        "selection_policy": (
            "up to five highest-scoring known defects + six highest-scoring acceptable "
            "frames + burst neighbors"
        ),
        "max_size": max_size,
        "pack_count": len(packs),
        "development_pack_count": sum(
            pack["split"] == "development" for pack in packs
        ),
        "holdout_pack_count": sum(pack["split"] == "holdout" for pack in packs),
        "image_count": sum(len(pack["files"]) for pack in packs),
        "known_defect_count": sum(len(pack["known_defect_ids"]) for pack in packs),
        "burst_candidate_count": sum(
            len(pack["burst_duplicate_seed"]) for pack in packs
        ),
        "packs": packs,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen-manifest", type=Path, required=True)
    parser.add_argument("--defects", type=Path, required=True)
    parser.add_argument("--acceptable", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-size", type=int, default=15)
    parser.add_argument("--base-size", type=int, default=13)
    parser.add_argument("--duplicate-candidates", type=int, default=2)
    parser.add_argument("--max-frame-gap", type=int, default=25)
    parser.add_argument("--max-hamming", type=int, default=18)
    args = parser.parse_args()
    result = build_hard_negative_manifest(
        frozen_manifest_path=args.frozen_manifest,
        defects_path=args.defects,
        acceptable_path=args.acceptable,
        predictions_path=args.predictions,
        output_path=args.output,
        max_size=max(8, args.max_size),
        base_size=max(8, min(args.base_size, args.max_size)),
        duplicate_candidates=max(0, args.duplicate_candidates),
        max_frame_gap=max(1, args.max_frame_gap),
        max_hamming=max(0, args.max_hamming),
    )
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "pack_count",
                    "development_pack_count",
                    "holdout_pack_count",
                    "image_count",
                    "known_defect_count",
                    "burst_candidate_count",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
