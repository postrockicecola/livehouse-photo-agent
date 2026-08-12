#!/usr/bin/env python3
"""Assemble the reviewed 50/50/100/50 selection-evaluation dataset."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.operators.stage2_prefilter import hamming_64


TARGETS = {
    "technical_hard": 50,
    "semantic_defect": 50,
    "ordinary": 100,
    "highlight": 50,
}
CATEGORY_ORDER = tuple(TARGETS)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prune_orientation_review(path: Path, selected_files: set[str]) -> None:
    if not path.is_file():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("items") or {}
    if not isinstance(items, dict):
        raise ValueError(f"{path}: items must be an object")
    selected_keys = {file_id.casefold() for file_id in selected_files}
    filtered = {
        file_id: value
        for file_id, value in items.items()
        if file_id.casefold() in selected_keys
    }
    if len(filtered) == len(items):
        return
    raw["items"] = filtered
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _frame_number(file_id: str) -> int | None:
    match = re.search(r"(\d+)(?=\.[^.]+$)", file_id)
    return int(match.group(1)) if match else None


def _technical_reasons(dims: dict[str, Any], suggestion: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if float(dims.get("focus_sharpness", 10)) <= 3:
        reasons.append("out_of_focus")
    if float(dims.get("exposure_control", 10)) <= 3:
        reasons.append("poor_exposure")
    if float(dims.get("noise_cleanliness", 10)) <= 3:
        reasons.append("excessive_noise")
    if float(dims.get("deliverable_subject", 10)) <= 2:
        reasons.append("undeliverable_subject")
    if not reasons:
        reasons.append("technical_failure")
    return reasons


def _semantic_reasons(suggestion: dict[str, Any]) -> list[str]:
    semantic = suggestion.get("semantic_defect")
    if isinstance(semantic, dict):
        reasons = [str(value) for value in semantic.get("types") or [] if str(value)]
        if reasons:
            return reasons
    return ["semantic_selection_failure"]


def _round_pool(round_dir: Path) -> list[dict[str, Any]]:
    candidates = {
        str(row["file"]).casefold(): row
        for row in _read_jsonl(round_dir / "candidates.jsonl")
    }
    suggestions = {
        str(row["file"]).casefold(): row
        for row in _read_jsonl(round_dir / "qwen_suggestions.jsonl")
    }
    reviews = {
        str(row["file"]).casefold(): row
        for row in _read_jsonl(round_dir / "human_reviews.jsonl")
    }
    entries: list[dict[str, Any]] = []
    for triage in _read_jsonl(round_dir / "provisional_triage.jsonl"):
        key = str(triage["file"]).casefold()
        candidate = candidates.get(key)
        suggestion = suggestions.get(key)
        review = reviews.get(key)
        if not candidate or not suggestion or not review:
            continue
        category = str(triage["provisional_category"])
        if category not in TARGETS:
            continue
        expected_keep = category == "highlight"
        if review.get("keep") is not expected_keep:
            continue
        historical = candidate.get("historical") or {}
        semantic = suggestion.get("semantic_defect")
        severity = (
            str(semantic.get("severity") or "")
            if isinstance(semantic, dict)
            else ""
        )
        entries.append(
            {
                "file": str(review["file"]),
                "source_path": str(candidate["source_path"]),
                "session": str(candidate.get("session") or ""),
                "sample_type": category,
                "overall": float(review["overall"]),
                "dims": dict(review["dims"]),
                "keep": bool(review["keep"]),
                "notes": str(review.get("notes") or ""),
                "phash": int(historical.get("phash") or 0),
                "defect_reasons": (
                    _technical_reasons(review["dims"], suggestion)
                    if category == "technical_hard"
                    else _semantic_reasons(suggestion)
                    if category == "semantic_defect"
                    else []
                ),
                "rank": {
                    "confidence": float(suggestion.get("confidence") or 0),
                    "severity": {"fatal": 3, "major": 2, "minor": 1}.get(
                        severity, 0
                    ),
                },
                "provenance": {
                    "source": round_dir.name,
                    "human_reviewed": True,
                    "human_keep": bool(review["keep"]),
                    "category_basis": "cloud_triage_plus_human_keep_decision",
                    "model": suggestion.get("model"),
                    "prompt_sha": suggestion.get("prompt_sha"),
                    "semantic_defect": semantic,
                },
            }
        )
    return entries


def _existing_technical_pool(
    labels_path: Path, manifest_path: Path
) -> list[dict[str, Any]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = {
        str(item.get("file") or "").casefold(): item
        for item in manifest.get("items") or []
    }
    entries: list[dict[str, Any]] = []
    for row in _read_jsonl(labels_path):
        dims = row.get("dims") or {}
        if not any(
            float(dims.get(key, 10)) <= 2
            for key in (
                "focus_sharpness",
                "exposure_control",
                "deliverable_subject",
            )
        ):
            continue
        source = sources.get(str(row.get("file") or "").casefold())
        if source is None or not Path(str(source.get("source_path") or "")).is_file():
            continue
        entries.append(
            {
                "file": str(row["file"]),
                "source_path": str(source["source_path"]),
                "session": str(source.get("session") or ""),
                "sample_type": "technical_hard",
                "overall": float(row["overall"]),
                "dims": dict(dims),
                "keep": False,
                "notes": str(row.get("notes") or ""),
                "phash": 0,
                "defect_reasons": _technical_reasons(dims, {}),
                "rank": {"confidence": 0.5, "severity": 3},
                "provenance": {
                    "source": "golden_apr_jul_2026",
                    "human_reviewed": True,
                    "human_keep": bool(row.get("keep")),
                    "category_basis": "strict_human_dimension_threshold",
                    "model": None,
                    "prompt_sha": None,
                    "semantic_defect": None,
                },
            }
        )
    return entries


def _score(category: str, entry: dict[str, Any], jitter: float) -> float:
    overall = float(entry["overall"])
    confidence = float((entry.get("rank") or {}).get("confidence") or 0)
    severity = float((entry.get("rank") or {}).get("severity") or 0)
    if category == "semantic_defect":
        return severity * 100 + confidence * 20 - overall * 0.05 + jitter
    if category == "technical_hard":
        return (100 - overall) + confidence * 10 + jitter
    if category == "highlight":
        return overall + confidence * 5 + jitter
    # Favor the center of the usable, non-highlight band.
    return 100 - abs(overall - 63) + jitter


def _conflicts(
    entry: dict[str, Any],
    selected: list[dict[str, Any]],
    *,
    max_hamming: int,
    min_file_number_gap: int,
) -> bool:
    if entry.get("allow_near_duplicate"):
        return False
    frame = _frame_number(str(entry["file"]))
    phash = int(entry.get("phash") or 0)
    for other in selected:
        if other.get("allow_near_duplicate"):
            continue
        if entry["session"] != other["session"]:
            continue
        other_frame = _frame_number(str(other["file"]))
        other_phash = int(other.get("phash") or 0)
        if (
            frame is not None
            and other_frame is not None
            and abs(frame - other_frame) < min_file_number_gap
            and (not phash or not other_phash)
        ):
            return True
        if phash and other_phash and hamming_64(phash, other_phash) <= max_hamming:
            return True
    return False


def _select_category(
    pool: list[dict[str, Any]],
    *,
    category: str,
    count: int,
    selected: list[dict[str, Any]],
    session_cap: int,
    rng: random.Random,
) -> list[dict[str, Any]]:
    ranked = sorted(
        pool,
        key=lambda entry: _score(category, entry, rng.random() * 0.01),
        reverse=True,
    )
    chosen: list[dict[str, Any]] = []
    session_counts: Counter[str] = Counter()
    for cap in (session_cap, session_cap + 2, 10_000):
        for entry in ranked:
            if len(chosen) >= count:
                return chosen
            if entry in chosen or session_counts[entry["session"]] >= cap:
                continue
            if _conflicts(
                entry,
                [*selected, *chosen],
                max_hamming=12,
                min_file_number_gap=25,
            ):
                continue
            chosen.append(entry)
            session_counts[entry["session"]] += 1
    if len(chosen) != count:
        raise ValueError(f"{category}: selected {len(chosen)} of required {count}")
    return chosen


def assemble(
    *,
    labels_path: Path,
    manifest_path: Path,
    round_dirs: list[Path],
    output_dir: Path,
    seed: int,
    excluded_files: set[str] | None = None,
    allow_near_duplicate_files: set[str] | None = None,
    included_files: set[str] | None = None,
) -> dict[str, Any]:
    excluded_keys = {file_id.casefold() for file_id in excluded_files or set()}
    included_keys = {file_id.casefold() for file_id in included_files or set()}
    if excluded_keys & included_keys:
        raise ValueError("the same file cannot be both included and excluded")
    allowed_duplicate_keys = {
        file_id.casefold() for file_id in allow_near_duplicate_files or set()
    }
    pools: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for round_dir in round_dirs:
        for entry in _round_pool(round_dir):
            if entry["file"].casefold() not in excluded_keys:
                pools[entry["sample_type"]].append(entry)
    pools["technical_hard"].extend(
        entry
        for entry in _existing_technical_pool(labels_path, manifest_path)
        if entry["file"].casefold() not in excluded_keys
    )
    for entries in pools.values():
        for entry in entries:
            if entry["file"].casefold() in allowed_duplicate_keys:
                entry["allow_near_duplicate"] = True

    rng = random.Random(seed)
    selected: list[dict[str, Any]] = []
    available_by_key = {
        entry["file"].casefold(): entry
        for entries in pools.values()
        for entry in entries
    }
    missing_includes = included_keys - set(available_by_key)
    if missing_includes:
        raise ValueError(f"included files not found: {sorted(missing_includes)}")
    # Scarce defect classes reserve their frames before broad positive classes.
    for category, cap in (
        ("technical_hard", 8),
        ("semantic_defect", 6),
        ("ordinary", 8),
        ("highlight", 5),
    ):
        pinned = [
            entry
            for entry in pools[category]
            if entry["file"].casefold() in included_keys
        ]
        if len(pinned) > TARGETS[category]:
            raise ValueError(f"{category}: too many explicitly included files")
        selected.extend(pinned)
        selected.extend(
            _select_category(
                [entry for entry in pools[category] if entry not in pinned],
                category=category,
                count=TARGETS[category] - len(pinned),
                selected=selected,
                session_cap=cap,
                rng=rng,
            )
        )
    selected.sort(key=lambda row: (CATEGORY_ORDER.index(row["sample_type"]), row["file"]))

    output_dir.mkdir(parents=True, exist_ok=True)
    images_dir = output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    expected_links: set[str] = set()
    manifest_items: list[dict[str, Any]] = []
    for entry in selected:
        source = Path(entry["source_path"])
        if not source.is_file():
            raise FileNotFoundError(source)
        link = images_dir / entry["file"]
        expected_links.add(link.name.casefold())
        if link.is_symlink() and link.resolve() != source.resolve():
            link.unlink()
        elif link.exists() and not link.is_symlink():
            raise FileExistsError(link)
        if not link.exists():
            link.symlink_to(source.resolve())
        manifest_items.append(
            {
                "file": entry["file"],
                "source_path": str(source),
                "session": entry["session"],
                "sha256": _sha256(source),
                "sample_type": entry["sample_type"],
                "defect_reasons": entry["defect_reasons"],
                "provenance": entry["provenance"],
            }
        )
    for link in images_dir.iterdir():
        if link.is_symlink() and link.name.casefold() not in expected_links:
            link.unlink()

    with (output_dir / "labels.jsonl").open("w", encoding="utf-8") as handle:
        for entry in selected:
            handle.write(
                json.dumps(
                    {
                        "file": entry["file"],
                        "overall": entry["overall"],
                        "dims": entry["dims"],
                        "keep": entry["keep"],
                        "notes": entry["notes"],
                        "sample_type": entry["sample_type"],
                        "defect_reasons": entry["defect_reasons"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "selection_eval_manifest.v1",
                "seed": seed,
                "target": len(selected),
                "items": manifest_items,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    highlights = [
        entry["file"] for entry in selected if entry["sample_type"] == "highlight"
    ]
    (output_dir / "acceptable_pool.json").write_text(
        json.dumps(highlights, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    defects = {
        entry["file"]: {
            "is_defect": True,
            "sample_type": entry["sample_type"],
            "reasons": entry["defect_reasons"],
            "overall_score": entry["overall"],
        }
        for entry in selected
        if entry["sample_type"] in {"technical_hard", "semantic_defect"}
    }
    (output_dir / "defects.json").write_text(
        json.dumps(defects, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output_dir / "items.jsonl").open("w", encoding="utf-8") as handle:
        for entry in selected:
            handle.write(
                json.dumps(
                    {
                        "file": entry["file"],
                        "source_path": entry["source_path"],
                        "session": entry["session"],
                        "target_category": entry["sample_type"],
                        "mining_score": entry["overall"],
                        "selection_reasons": entry["defect_reasons"],
                        "historical": {
                            "overall_score": entry["overall"],
                            "dimensions": entry["dims"],
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    summary = {
        "schema_version": "selection_eval_dataset.v1",
        "total": len(selected),
        "counts": dict(Counter(entry["sample_type"] for entry in selected)),
        "sessions": len({entry["session"] for entry in selected}),
        "source_counts": dict(
            Counter(entry["provenance"]["source"] for entry in selected)
        ),
        "human_reviewed": sum(
            bool(entry["provenance"]["human_reviewed"]) for entry in selected
        ),
        "manually_excluded": sorted(excluded_files or set()),
        "near_duplicate_exceptions": sorted(allow_near_duplicate_files or set()),
        "manually_included": sorted(included_files or set()),
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _prune_orientation_review(
        output_dir / "orientation_review.json",
        {entry["file"] for entry in selected},
    )
    return summary


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=root / "data/eval/labels.jsonl")
    parser.add_argument(
        "--manifest", type=Path, default=root / "data/eval/manifest.json"
    )
    parser.add_argument("--round", type=Path, action="append", required=True)
    parser.add_argument(
        "--output", type=Path, default=root / "data/eval/selection_v1"
    )
    parser.add_argument("--seed", type=int, default=20260809)
    parser.add_argument(
        "--exclude-file",
        action="append",
        default=[],
        help="Case-insensitive file id to exclude; may be repeated",
    )
    parser.add_argument(
        "--allow-near-duplicate-file",
        action="append",
        default=[],
        help="Explicitly permit a reviewed replacement despite burst deduplication",
    )
    parser.add_argument(
        "--include-file",
        action="append",
        default=[],
        help="Explicitly include a reviewed candidate; may be repeated",
    )
    args = parser.parse_args()
    summary = assemble(
        labels_path=args.labels,
        manifest_path=args.manifest,
        round_dirs=args.round,
        output_dir=args.output,
        seed=args.seed,
        excluded_files=set(args.exclude_file),
        allow_near_duplicate_files=set(args.allow_near_duplicate_file),
        included_files=set(args.include_file),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
