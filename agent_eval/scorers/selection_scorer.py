"""Hard-gate and overlap metrics for photo-selection agent evaluations."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DEFAULT_DEFECTS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "eval" / "agent" / "defects.json"
)
_PHOTO_ID_RE = re.compile(
    r"(?<![\w.-])([A-Za-z0-9][A-Za-z0-9_.-]*\.(?:jpe?g|png|webp))(?![\w.])",
    re.IGNORECASE,
)


def extract_selected_photo_ids(text: str, max_k: int = 10) -> list[str]:
    """Extract unique photo filenames in their original order."""
    if max_k <= 0:
        return []

    selected: list[str] = []
    seen: set[str] = set()
    for match in _PHOTO_ID_RE.finditer(text or ""):
        photo_id = match.group(1)
        key = photo_id.casefold()
        if key in seen:
            continue
        seen.add(key)
        selected.append(photo_id)
        if len(selected) >= max_k:
            break
    return selected


@dataclass(frozen=True)
class DefectViolation:
    photo_id: str
    reasons: list[str]
    overall_score: float | None


@dataclass(frozen=True)
class DefectEvalResult:
    selected_count: int
    defect_count: int
    defect_rate: float
    is_passed: bool
    violations: list[DefectViolation]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SelectionMetrics:
    selected_count: int
    k: int
    defect_count: int
    defect_rate: float
    overlap_count: int
    overlap_at_k: float
    min_overlap_threshold: float
    is_passed: bool
    violations: list[DefectViolation]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _unique_ids(photo_ids: list[str], limit: int | None = None) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for photo_id in photo_ids:
        value = str(photo_id)
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
        if limit is not None and len(unique) >= limit:
            break
    return unique


class DefectZeroToleranceEvaluator:
    """Fail a selection if it is empty or contains any known defect."""

    def __init__(self, defects_path: str | Path = DEFAULT_DEFECTS_PATH) -> None:
        path = Path(defects_path)
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a JSON object")
        self.defects = {
            str(photo_id).casefold(): record
            for photo_id, record in raw.items()
            if isinstance(record, dict) and record.get("is_defect") is True
        }

    def evaluate(self, selected_ids: list[str]) -> DefectEvalResult:
        selected = _unique_ids(selected_ids)
        violations: list[DefectViolation] = []
        for photo_id in selected:
            record = self.defects.get(photo_id.casefold())
            if record is None:
                continue
            score = record.get("overall_score")
            violations.append(
                DefectViolation(
                    photo_id=photo_id,
                    reasons=[str(reason) for reason in record.get("reasons") or []],
                    overall_score=(
                        float(score)
                        if isinstance(score, (int, float)) and not isinstance(score, bool)
                        else None
                    ),
                )
            )

        defect_count = len(violations)
        return DefectEvalResult(
            selected_count=len(selected),
            defect_count=defect_count,
            defect_rate=defect_count / len(selected) if selected else 0.0,
            is_passed=bool(selected) and defect_count == 0,
            violations=violations,
        )


class SelectionScorer:
    """Combine the zero-defect hard gate with expert-pool Overlap@K."""

    def __init__(
        self,
        defects_path: str | Path = DEFAULT_DEFECTS_PATH,
        min_overlap_threshold: float = 0.80,
    ) -> None:
        if not 0.0 <= min_overlap_threshold <= 1.0:
            raise ValueError("min_overlap_threshold must be between 0 and 1")
        self.defect_evaluator = DefectZeroToleranceEvaluator(defects_path)
        self.min_overlap_threshold = min_overlap_threshold

    def score(
        self,
        selected_ids: list[str],
        acceptable_pool: list[str],
        k: int = 10,
    ) -> SelectionMetrics:
        if k <= 0:
            raise ValueError("k must be greater than zero")

        selected = _unique_ids(selected_ids, limit=k)
        defect_result = self.defect_evaluator.evaluate(selected)
        acceptable = {str(photo_id).casefold() for photo_id in acceptable_pool}
        overlap_count = sum(
            1 for photo_id in selected if photo_id.casefold() in acceptable
        )
        overlap_at_k = overlap_count / k
        is_passed = (
            defect_result.is_passed
            and defect_result.defect_rate == 0.0
            and overlap_at_k >= self.min_overlap_threshold
        )
        return SelectionMetrics(
            selected_count=len(selected),
            k=k,
            defect_count=defect_result.defect_count,
            defect_rate=defect_result.defect_rate,
            overlap_count=overlap_count,
            overlap_at_k=overlap_at_k,
            min_overlap_threshold=self.min_overlap_threshold,
            is_passed=is_passed,
            violations=defect_result.violations,
        )
