#!/usr/bin/env python3
"""Triage cloud VLM suggestions into provisional human-review buckets."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


TECHNICAL_DIMS = ("focus_sharpness", "exposure_control", "noise_cleanliness")
SEMANTIC_DIMS = ("deliverable_subject", "composition_framing", "moment_peak")
TECHNICAL_TERMS = re.compile(
    r"失焦|模糊|过曝|曝光|死黑|细节.*(?:丢失|全无|崩塌)|技术性失败|可弃帧"
)
OUT_OF_DOMAIN_TERMS = re.compile(
    r"城市夜景|高架路|街景|车流|建筑|窗外景|无演出主体|无表演者|"
    r"偏离livehouse|非livehouse"
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON ({exc})") from exc
            if isinstance(row, dict):
                rows.append(row)
    return rows


def provisional_category(suggestion: dict[str, Any]) -> tuple[str, list[str]]:
    """Apply conservative, mutually exclusive pre-review rules."""
    dims = suggestion.get("dims") or {}
    text = " ".join(
        [
            str(suggestion.get("reason") or ""),
            str(suggestion.get("reason_technical") or ""),
            str(suggestion.get("weakest_aspect") or ""),
            " ".join(str(tag) for tag in suggestion.get("tags") or []),
        ]
    )
    reasons: list[str] = []
    for dimension in TECHNICAL_DIMS:
        value = dims.get(dimension)
        if isinstance(value, (int, float)) and value <= 2.0:
            reasons.append(f"{dimension}={value:.1f}<=2")
    if reasons:
        return "technical_hard", reasons

    if OUT_OF_DOMAIN_TERMS.search(text):
        return "out_of_domain", ["non_livehouse_content"]

    semantic = suggestion.get("semantic_defect")
    if (
        isinstance(semantic, dict)
        and semantic.get("is_present") is True
        and str(semantic.get("severity") or "").casefold() in {"major", "fatal"}
    ):
        defect_types = [
            str(value) for value in semantic.get("types") or [] if str(value)
        ]
        return "semantic_defect", [
            f"semantic_severity={semantic.get('severity')}",
            f"semantic_types={','.join(defect_types) or 'other'}",
        ]

    for dimension in SEMANTIC_DIMS:
        value = dims.get(dimension)
        if isinstance(value, (int, float)) and value <= 2.0:
            reasons.append(f"{dimension}={value:.1f}<=2")
    if reasons:
        return "semantic_defect", reasons

    overall = suggestion.get("overall")
    if isinstance(overall, (int, float)) and overall < 45 and TECHNICAL_TERMS.search(text):
        return "technical_hard", [
            f"overall={overall:.1f}<45",
            "technical_failure_text",
        ]

    focus = dims.get("focus_sharpness")
    subject = dims.get("deliverable_subject")
    moment = dims.get("moment_peak")
    if (
        isinstance(overall, (int, float))
        and overall >= 75
        and all(
            isinstance(value, (int, float)) and value >= 6
            for value in (focus, subject, moment)
        )
    ):
        return "highlight", [
            f"overall={overall:.1f}>=75",
            "focus_subject_moment>=6",
        ]

    numeric_dims = [value for value in dims.values() if isinstance(value, (int, float))]
    if (
        isinstance(overall, (int, float))
        and 50 <= overall < 75
        and len(numeric_dims) == 8
        and min(numeric_dims) >= 4
    ):
        return "ordinary", [
            f"overall={overall:.1f}",
            f"min_dimension={min(numeric_dims):.1f}>=4",
        ]
    return "uncertain", ["requires_manual_classification"]


def build_triage(
    candidates_path: Path,
    suggestions_path: Path,
) -> list[dict[str, Any]]:
    candidates = {
        str(row.get("file") or "").casefold(): row
        for row in _read_jsonl(candidates_path)
        if row.get("file")
    }
    triaged: list[dict[str, Any]] = []
    for suggestion in _read_jsonl(suggestions_path):
        file_id = str(suggestion.get("file") or "")
        candidate = candidates.get(file_id.casefold())
        if candidate is None:
            continue
        category, reasons = provisional_category(suggestion)
        triaged.append(
            {
                "file": file_id,
                "source_path": candidate.get("source_path"),
                "session": candidate.get("session"),
                "mined_target_category": candidate.get("target_category"),
                "provisional_category": category,
                "triage_reasons": reasons,
                "qwen": {
                    "overall": suggestion.get("overall"),
                    "dims": suggestion.get("dims"),
                    "confidence": suggestion.get("confidence"),
                    "reason": suggestion.get("reason"),
                    "reason_technical": suggestion.get("reason_technical"),
                    "semantic_defect": suggestion.get("semantic_defect"),
                    "tags": suggestion.get("tags"),
                    "model": suggestion.get("model"),
                    "prompt_sha": suggestion.get("prompt_sha"),
                },
                "human_reviewed": False,
            }
        )
    return triaged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("suggestions", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.suggestions.with_name("provisional_triage.jsonl")
    rows = build_triage(args.candidates, args.suggestions)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = Counter(row["provisional_category"] for row in rows)
    summary = {
        "schema_version": "selection_candidate_triage.v1",
        "total": len(rows),
        "counts": dict(counts),
        "warning": "All categories are provisional until human review.",
    }
    output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for category in (
        "technical_hard",
        "semantic_defect",
        "ordinary",
        "highlight",
        "out_of_domain",
        "uncertain",
    ):
        files = [
            str(row["file"])
            for row in rows
            if row["provisional_category"] == category
        ]
        output.with_name(f"{category}.txt").write_text(
            "\n".join(files) + ("\n" if files else ""),
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
