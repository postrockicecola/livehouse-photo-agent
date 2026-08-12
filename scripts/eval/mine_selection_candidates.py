#!/usr/bin/env python3
"""Mine a deficit-driven photo batch for cloud VLM pre-labeling.

Historical model outputs are used only to find high-information candidates.
The emitted ``target_category`` is a sampling stratum, not ground truth.

Example::

    python scripts/eval/mine_selection_candidates.py \
      --archive-root /Volumes/M4Buffer/Visions/Livehouse_Archive \
      --include-session '^2026-(05|06|07)' \
      --output data/eval/candidate_rounds/round_001/candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

import cv2

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.operators.stage2_prefilter import hamming_64
from scripts.eval.labels import load_labels, normalize_name


DEFAULT_QUOTAS = {
    "technical_hard": 60,
    "semantic_defect": 80,
    "ordinary": 30,
    "highlight": 30,
}
VALID_CATEGORIES = tuple(DEFAULT_QUOTAS)
SEMANTIC_TERMS = {
    "closed_eyes",
    "eyes_closed",
    "bad_expression",
    "crowd_dense",
    "occlusion",
    "obstruction",
    "闭眼",
    "表情",
    "无主体",
    "遮挡",
    "构图失衡",
}
TECHNICAL_TERMS = {
    "blur_laplacian_hard",
    "severe_blur",
    "out_of_focus",
    "extreme_blowout",
    "shadow_crush",
    "严重模糊",
    "失焦",
    "严重过曝",
    "死黑",
}


@dataclass(frozen=True)
class Candidate:
    session: str
    session_tag: str
    name: str
    source_path: Path
    historical_score: float | None
    fast_score: float | None
    tech_score: float | None
    dimensions: dict[str, float]
    debug_info: dict[str, Any]
    tags: list[str]
    text: str
    phash: int

    @property
    def file_id(self) -> str:
        return f"{self.session_tag}__{self.name}"


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _session_tag(session_dir: Path) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", session_dir.name)[:24] or "session"


def _frame_number(name: str) -> int | None:
    match = re.search(r"(\d+)(?=\.[^.]+$)", name)
    return int(match.group(1)) if match else None


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        data = data["results"]
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _load_jsonl_by_name(path: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            name = Path(str(row.get("file_name") or row.get("file") or "")).name
            if name:
                rows.setdefault(name.casefold(), row)
    return rows


def _dimensions(row: dict[str, Any]) -> dict[str, float]:
    raw = row.get("dimensions")
    if not isinstance(raw, dict):
        stage3 = row.get("stage3_result")
        raw = stage3.get("dimensions") if isinstance(stage3, dict) else {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, raw_value in raw.items()
        if (value := _number(raw_value)) is not None
    }


def _phash(row: dict[str, Any], stage2: dict[str, Any]) -> int:
    for source in (
        row,
        row.get("debug_info"),
        stage2,
        stage2.get("debug_info"),
    ):
        if not isinstance(source, dict):
            continue
        try:
            value = int(source.get("phash") or 0)
        except (TypeError, ValueError):
            continue
        if value:
            return value
    return 0


def load_session_candidates(session_dir: Path) -> list[Candidate]:
    """Join archived gallery rows with optional Stage2 sidecar metrics."""
    previews = session_dir / "Previews"
    result_rows = _load_json_rows(previews / "analysis_results.json")
    stage2_rows = _load_jsonl_by_name(
        previews / ".luma_pipeline_staged" / "eligible_after_stage2.jsonl"
    )
    candidates: list[Candidate] = []
    seen: set[str] = set()
    for row in result_rows:
        name = Path(str(row.get("file") or row.get("file_name") or "")).name
        key = name.casefold()
        source_path = previews / name
        if not name or key in seen or not source_path.is_file():
            continue
        seen.add(key)
        stage2 = stage2_rows.get(key, {})
        debug = {}
        for source in (row.get("debug_info"), stage2.get("debug_info")):
            if isinstance(source, dict):
                debug.update(source)
        scores = row.get("scores")
        if isinstance(scores, dict) and "laplacian_var" not in debug:
            laplacian = _number(scores.get("laplacian"))
            if laplacian is not None:
                debug["laplacian_var"] = laplacian
        tags = [
            str(tag)
            for tag in [
                *(row.get("tags") or []),
                *(row.get("mood_tags") or []),
                *(debug.get("ambiguous_tags") or []),
            ]
        ]
        text = " ".join(
            str(row.get(key) or "")
            for key in ("reason", "weakness", "reason_bilingual", "weakness_bilingual")
        ).casefold()
        score = _number(row.get("overall_score"))
        if score is None and isinstance(row.get("scores"), dict):
            score = _number(row["scores"].get("overall"))
        candidates.append(
            Candidate(
                session=session_dir.name,
                session_tag=_session_tag(session_dir),
                name=name,
                source_path=source_path,
                historical_score=score,
                fast_score=_number(stage2.get("fast_score")),
                tech_score=_number(stage2.get("tech_score")),
                dimensions=_dimensions(row),
                debug_info=debug,
                tags=tags,
                text=text,
                phash=_phash(row, stage2),
            )
        )
    return candidates


def enrich_face_signals(
    candidates: list[Candidate],
    *,
    excluded: set[str],
    cache_path: Path,
    max_side: int = 640,
) -> tuple[list[Candidate], int]:
    """Detect visible faces in technically usable mid-score frames, with a cache."""
    cached: dict[str, dict[str, Any]] = {}
    if cache_path.is_file():
        for row in _load_json_rows(cache_path):
            if row.get("file"):
                cached[str(row["file"]).casefold()] = row

    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    if cascade.empty():
        raise RuntimeError("OpenCV frontal-face cascade is unavailable")

    scan_pool: list[Candidate] = []
    for candidate in candidates:
        if normalize_name(candidate.file_id) in excluded:
            continue
        laplacian = _number(candidate.debug_info.get("laplacian_var"))
        tags = {tag.casefold() for tag in candidate.tags}
        score = candidate.historical_score
        if (
            score is not None
            and 45 <= score <= 82
            and (laplacian is None or laplacian >= 18)
            and "stage2_prefilter" not in tags
        ):
            scan_pool.append(candidate)

    scanned = 0
    for candidate in scan_pool:
        key = candidate.file_id.casefold()
        if key in cached:
            continue
        gray = cv2.imread(str(candidate.source_path), cv2.IMREAD_GRAYSCALE)
        if gray is None:
            cached[key] = {
                "file": candidate.file_id,
                "face_count": 0,
                "face_area_ratio": 0.0,
            }
            continue
        height, width = gray.shape
        scale = min(1.0, max_side / max(height, width))
        if scale < 1.0:
            gray = cv2.resize(
                gray,
                (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        equalized = cv2.equalizeHist(gray)
        faces = cascade.detectMultiScale(
            equalized,
            scaleFactor=1.1,
            minNeighbors=4,
            minSize=(32, 32),
        )
        area = float(gray.shape[0] * gray.shape[1])
        max_face_ratio = max(
            (float(face_width * face_height) / area for _, _, face_width, face_height in faces),
            default=0.0,
        )
        cached[key] = {
            "file": candidate.file_id,
            "face_count": int(len(faces)),
            "face_area_ratio": round(max_face_ratio, 6),
        }
        scanned += 1

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(list(cached.values()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    enriched: list[Candidate] = []
    for candidate in candidates:
        signal = cached.get(candidate.file_id.casefold())
        if signal is None:
            enriched.append(candidate)
            continue
        debug = dict(candidate.debug_info)
        debug["face_count"] = int(signal.get("face_count") or 0)
        debug["face_area_ratio"] = float(signal.get("face_area_ratio") or 0.0)
        enriched.append(replace(candidate, debug_info=debug))
    return enriched, scanned


def _signal_blob(candidate: Candidate) -> str:
    return " ".join([candidate.text, *candidate.tags]).casefold()


def category_scores(
    candidate: Candidate,
    *,
    semantic_dim_ceiling: float = 4.0,
) -> dict[str, tuple[float, list[str]]]:
    """Return candidate-ranking scores and auditable mining reasons."""
    debug = candidate.debug_info
    dims = candidate.dimensions
    blob = _signal_blob(candidate)
    laplacian = _number(
        debug.get("laplacian_var", debug.get("stage2_laplacian_var"))
    )
    highlight_frac = _number(debug.get("highlight_frac")) or 0.0
    shadow_frac = _number(debug.get("shadow_frac")) or 0.0
    composition = _number(debug.get("composition_score"))
    face_count = _number(debug.get("face_count"))
    face_area_ratio = _number(debug.get("face_area_ratio")) or 0.0
    bright_subject = _number(debug.get("bright_subject_frac"))

    technical_reasons: list[str] = []
    technical = 0.0
    if laplacian is not None and laplacian < 18:
        technical += min(60.0, 18.0 - laplacian) * 4
        technical_reasons.append(f"laplacian={laplacian:.2f}<18")
    if highlight_frac >= 0.38:
        technical += highlight_frac * 100
        technical_reasons.append(f"highlight_frac={highlight_frac:.3f}")
    if shadow_frac >= 0.50:
        technical += shadow_frac * 100
        technical_reasons.append(f"shadow_frac={shadow_frac:.3f}")
    if any(term in blob for term in TECHNICAL_TERMS):
        technical += 35
        technical_reasons.append("historical_technical_tag")
    for dimension in ("focus_sharpness", "exposure_control", "noise_cleanliness"):
        value = dims.get(dimension)
        if value is not None and value <= 3:
            technical += (4 - value) * 12
            technical_reasons.append(f"{dimension}={value:.1f}")

    semantic_reasons: list[str] = []
    semantic = 0.0
    matched_terms = sorted(term for term in SEMANTIC_TERMS if term in blob)
    if matched_terms:
        semantic += 30 + min(20, len(matched_terms) * 5)
        semantic_reasons.append(f"semantic_tags={','.join(matched_terms[:4])}")
    if face_count is not None and face_count >= 1:
        semantic += 18 + min(30.0, math.sqrt(face_area_ratio) * 180)
        semantic_reasons.append(
            f"visible_faces={int(face_count)},max_area={face_area_ratio:.4f}"
        )
        if candidate.historical_score is not None and candidate.historical_score < 70:
            semantic += (70 - candidate.historical_score) * 0.5
            semantic_reasons.append(
                f"mid_low_historical_score={candidate.historical_score:.1f}"
            )
    if composition is not None and composition < 45:
        semantic += 45 - composition
        semantic_reasons.append(f"composition_score={composition:.1f}")
    if bright_subject is not None and bright_subject < 0.01:
        semantic += 15
        semantic_reasons.append(f"bright_subject_frac={bright_subject:.4f}")
    for dimension in ("deliverable_subject", "composition_framing", "moment_peak"):
        value = dims.get(dimension)
        if value is not None and value <= semantic_dim_ceiling:
            semantic += (semantic_dim_ceiling + 1 - value) * 12
            semantic_reasons.append(
                f"{dimension}={value:.1f}<={semantic_dim_ceiling:.1f}"
            )
    # Prefer semantically suspicious frames whose technical evidence is not decisive.
    if technical >= 45:
        semantic *= 0.35
        semantic_reasons.append("downweighted_technical_overlap")

    ordinary_reasons: list[str] = []
    ordinary = 0.0
    historical = candidate.historical_score
    fast = candidate.fast_score
    tech = candidate.tech_score
    if historical is not None and 50 <= historical < 75:
        ordinary += 35 - abs(historical - 62.5)
        ordinary_reasons.append(f"historical_score={historical:.1f}")
    if fast is not None and 45 <= fast <= 75:
        ordinary += 18
        ordinary_reasons.append(f"fast_score={fast:.1f}")
    if tech is not None and tech >= 70:
        ordinary += 15
        ordinary_reasons.append(f"tech_score={tech:.1f}")
    if technical >= 25 or semantic >= 25:
        ordinary = 0.0
        ordinary_reasons.append("rejected_by_defect_signal")

    highlight_reasons: list[str] = []
    highlight = 0.0
    if historical is not None and historical >= 75:
        highlight += historical - 60
        highlight_reasons.append(f"historical_score={historical:.1f}")
    if fast is not None and fast >= 75:
        highlight += (fast - 65) * 0.8
        highlight_reasons.append(f"fast_score={fast:.1f}")
    high_dimensions = [
        value for value in dims.values() if isinstance(value, (int, float)) and value >= 7.5
    ]
    if high_dimensions:
        highlight += len(high_dimensions) * 3
        highlight_reasons.append(f"high_dimensions={len(high_dimensions)}")
    if technical >= 30 or semantic >= 35:
        highlight = 0.0
        highlight_reasons.append("rejected_by_defect_signal")

    return {
        "technical_hard": (technical, technical_reasons),
        "semantic_defect": (semantic, semantic_reasons),
        "ordinary": (ordinary, ordinary_reasons),
        "highlight": (highlight, highlight_reasons),
    }


def parse_quotas(values: Iterable[str]) -> dict[str, int]:
    quotas = dict(DEFAULT_QUOTAS)
    for value in values:
        try:
            category, raw_count = value.split("=", 1)
            count = int(raw_count)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid quota {value!r}; expected category=count") from exc
        if category not in VALID_CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        if count < 0:
            raise ValueError("quota cannot be negative")
        quotas[category] = count
    return quotas


def excluded_file_ids(labels_path: Path, manifests: Iterable[Path]) -> set[str]:
    excluded = {normalize_name(label.file) for label in load_labels(labels_path)}
    for path in manifests:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    excluded.add(normalize_name(str(row.get("file") or "")))
    return excluded


def select_candidates(
    candidates: list[Candidate],
    *,
    quotas: dict[str, int],
    excluded: set[str],
    seed: int,
    max_per_session: int,
    max_hamming: int,
    min_file_number_gap: int,
    semantic_dim_ceiling: float = 4.0,
) -> list[dict[str, Any]]:
    """Select category quotas with session caps and within-session pHash dedupe."""
    rng = random.Random(seed)
    ranked: dict[str, list[tuple[float, float, Candidate, list[str]]]] = {
        category: [] for category in VALID_CATEGORIES
    }
    for candidate in candidates:
        if normalize_name(candidate.file_id) in excluded:
            continue
        for category, (score, reasons) in category_scores(
            candidate,
            semantic_dim_ceiling=semantic_dim_ceiling,
        ).items():
            if score > 0:
                ranked[category].append((score, rng.random(), candidate, reasons))
    for rows in ranked.values():
        rows.sort(key=lambda item: (item[0], item[1]), reverse=True)

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    per_session: Counter[str] = Counter()
    hashes: dict[str, list[int]] = defaultdict(list)
    frame_numbers: dict[str, list[int]] = defaultdict(list)
    for category in VALID_CATEGORIES:
        target = quotas.get(category, 0)
        for score, _, candidate, reasons in ranked[category]:
            if sum(row["target_category"] == category for row in selected) >= target:
                break
            file_key = normalize_name(candidate.file_id)
            if file_key in selected_ids or per_session[candidate.session] >= max_per_session:
                continue
            if candidate.phash and any(
                hamming_64(candidate.phash, existing) <= max_hamming
                for existing in hashes[candidate.session]
            ):
                continue
            frame_number = _frame_number(candidate.name)
            if frame_number is not None and any(
                abs(frame_number - existing) < min_file_number_gap
                for existing in frame_numbers[candidate.session]
            ):
                continue
            selected_ids.add(file_key)
            per_session[candidate.session] += 1
            if candidate.phash:
                hashes[candidate.session].append(candidate.phash)
            if frame_number is not None:
                frame_numbers[candidate.session].append(frame_number)
            selected.append(
                {
                    "file": candidate.file_id,
                    "source_path": str(candidate.source_path),
                    "session": candidate.session,
                    "target_category": category,
                    "mining_score": round(score, 3),
                    "selection_reasons": reasons,
                    "historical": {
                        "overall_score": candidate.historical_score,
                        "fast_score": candidate.fast_score,
                        "tech_score": candidate.tech_score,
                        "dimensions": candidate.dimensions,
                        "debug_info": candidate.debug_info,
                        "tags": candidate.tags,
                        "phash": candidate.phash or None,
                    },
                    "label_status": "pending_cloud_prelabel",
                    "human_reviewed": False,
                }
            )
    return selected


def _discover_sessions(
    archive_root: Path,
    includes: list[re.Pattern[str]],
    excludes: list[re.Pattern[str]],
) -> list[Path]:
    sessions: list[Path] = []
    for session in sorted(path for path in archive_root.iterdir() if path.is_dir()):
        if session.name.startswith("."):
            continue
        if includes and not any(pattern.search(session.name) for pattern in includes):
            continue
        if any(pattern.search(session.name) for pattern in excludes):
            continue
        if (session / "Previews" / "analysis_results.json").is_file():
            sessions.append(session)
    return sessions


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument(
        "--labels", type=Path, default=REPO_ROOT / "data/eval/labels.jsonl"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--exclude-manifest", type=Path, action="append", default=[])
    parser.add_argument("--exclude-file-list", type=Path, action="append", default=[])
    parser.add_argument("--include-session", action="append", default=[])
    parser.add_argument("--exclude-session", action="append", default=[])
    parser.add_argument(
        "--quota",
        action="append",
        default=[],
        help="override category count, e.g. semantic_defect=80",
    )
    parser.add_argument("--max-per-session", type=int, default=12)
    parser.add_argument(
        "--max-hamming",
        type=int,
        default=12,
        help="maximum pHash distance treated as a near duplicate",
    )
    parser.add_argument(
        "--min-file-number-gap",
        type=int,
        default=25,
        help="minimum numeric filename gap within a session to suppress bursts",
    )
    parser.add_argument(
        "--semantic-face-scan-cache",
        type=Path,
        help="enable face-rich semantic mining and cache scan results here",
    )
    parser.add_argument(
        "--semantic-dim-ceiling",
        type=float,
        default=4.0,
        help=(
            "mine low deliverable/composition/moment dimensions up to this value; "
            "raise only for development-set recall"
        ),
    )
    parser.add_argument("--seed", type=int, default=20260809)
    args = parser.parse_args()

    quotas = parse_quotas(args.quota)
    includes = [re.compile(pattern) for pattern in args.include_session]
    excludes = [re.compile(pattern) for pattern in args.exclude_session]
    sessions = _discover_sessions(args.archive_root.expanduser(), includes, excludes)
    if not sessions:
        raise SystemExit("no matching sessions with analysis_results.json")

    candidates = [
        candidate
        for session in sessions
        for candidate in load_session_candidates(session)
    ]
    excluded_ids = excluded_file_ids(args.labels, args.exclude_manifest)
    for path in args.exclude_file_list:
        if path.is_file():
            excluded_ids.update(
                normalize_name(line.strip())
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    face_rows_scanned = 0
    if args.semantic_face_scan_cache:
        candidates, face_rows_scanned = enrich_face_signals(
            candidates,
            excluded=excluded_ids,
            cache_path=args.semantic_face_scan_cache,
        )
    selected = select_candidates(
        candidates,
        quotas=quotas,
        excluded=excluded_ids,
        seed=args.seed,
        max_per_session=max(1, args.max_per_session),
        max_hamming=max(0, min(32, args.max_hamming)),
        min_file_number_gap=max(0, args.min_file_number_gap),
        semantic_dim_ceiling=max(0.0, min(10.0, args.semantic_dim_ceiling)),
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in selected:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = Counter(row["target_category"] for row in selected)
    summary = {
        "schema_version": "selection_candidate_round.v1",
        "seed": args.seed,
        "semantic_dim_ceiling": args.semantic_dim_ceiling,
        "archive_root": str(args.archive_root),
        "sessions_scanned": len(sessions),
        "candidate_rows_scanned": len(candidates),
        "existing_labels_excluded": len(excluded_ids),
        "face_rows_scanned": face_rows_scanned,
        "requested_quotas": quotas,
        "dedupe": {
            "max_hamming": max(0, min(32, args.max_hamming)),
            "min_file_number_gap": max(0, args.min_file_number_gap),
            "max_per_session": max(1, args.max_per_session),
        },
        "selected_counts": dict(counts),
        "selected_total": len(selected),
        "warning": (
            "target_category and mining_score are model-derived sampling signals, "
            "not evaluation ground truth"
        ),
    }
    summary_path = args.output.with_suffix(".summary.json")
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"candidates: {args.output}")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
