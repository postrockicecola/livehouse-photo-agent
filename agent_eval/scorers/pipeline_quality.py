"""Pure metrics for the frozen photo-selection quality benchmark."""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any, Iterable


DEFECT_TYPES = {"technical_hard", "semantic_defect"}
CATEGORY_ORDER = ("technical_hard", "semantic_defect", "ordinary", "highlight")


def wilson_interval(successes: int, total: int, z: float = 1.96) -> dict[str, float]:
    """Return a Wilson score interval for a binomial proportion."""
    if total <= 0 or successes < 0 or successes > total:
        raise ValueError("successes and total must satisfy 0 <= successes <= total")
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total + z * z / (4 * total * total)
        )
        / denominator
    )
    return {
        "value": round(proportion, 6),
        "low": round(max(0.0, center - margin), 6),
        "high": round(min(1.0, center + margin), 6),
    }


def binary_metrics(
    truth_positive: Iterable[bool], predicted_positive: Iterable[bool]
) -> dict[str, Any]:
    truth = list(truth_positive)
    predicted = list(predicted_positive)
    if len(truth) != len(predicted) or not truth:
        raise ValueError("truth and predictions must be aligned and non-empty")
    tp = sum(actual and guess for actual, guess in zip(truth, predicted))
    fn = sum(actual and not guess for actual, guess in zip(truth, predicted))
    fp = sum(not actual and guess for actual, guess in zip(truth, predicted))
    tn = sum(not actual and not guess for actual, guess in zip(truth, predicted))
    recall = wilson_interval(tp, tp + fn) if tp + fn else None
    specificity = wilson_interval(tn, tn + fp) if tn + fp else None
    precision = wilson_interval(tp, tp + fp) if tp + fp else None
    return {
        "counts": {"tp": tp, "fn": fn, "fp": fp, "tn": tn, "total": len(truth)},
        "recall": recall,
        "false_negative_rate": round(fn / (tp + fn), 6) if tp + fn else None,
        "false_positive_rate": round(fp / (fp + tn), 6) if fp + tn else None,
        "specificity": specificity,
        "precision": precision,
    }


def _prediction_map(predictions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in predictions:
        file_id = str(row.get("file") or "")
        if not file_id or file_id in result:
            raise ValueError(f"duplicate or empty prediction file id: {file_id!r}")
        result[file_id] = row
    return result


def _class_rejection(
    labels: list[dict[str, Any]],
    rejected: dict[str, bool],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for category in CATEGORY_ORDER:
        members = [row for row in labels if row.get("sample_type") == category]
        count = sum(bool(rejected[str(row["file"])]) for row in members)
        output[category] = {
            "rejected": count,
            "total": len(members),
            "rate": wilson_interval(count, len(members)),
        }
    return output


def _reason_recall(
    labels: list[dict[str, Any]],
    rejected: dict[str, bool],
    category: str,
) -> dict[str, Any]:
    reason_members: dict[str, list[str]] = defaultdict(list)
    for row in labels:
        if row.get("sample_type") != category:
            continue
        for reason in row.get("defect_reasons") or []:
            reason_members[str(reason)].append(str(row["file"]))
    return {
        reason: {
            "caught": sum(rejected[file_id] for file_id in members),
            "total": len(members),
            "recall": wilson_interval(
                sum(rejected[file_id] for file_id in members), len(members)
            ),
        }
        for reason, members in sorted(reason_members.items())
    }


def evaluate_pipeline(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate technical filtering, final semantic gate, and final rejection."""
    by_file = _prediction_map(predictions)
    label_ids = {str(row["file"]) for row in labels}
    if set(by_file) != label_ids:
        missing = sorted(label_ids - set(by_file))
        extra = sorted(set(by_file) - label_ids)
        raise ValueError(f"prediction ids differ from labels; missing={missing}, extra={extra}")

    stage1 = {file_id: bool(row.get("stage1_reject")) for file_id, row in by_file.items()}
    semantic = {
        file_id: bool(row.get("semantic_reject", row.get("stage2_reject")))
        for file_id, row in by_file.items()
    }
    final = {
        file_id: stage1[file_id] or (not stage1[file_id] and semantic[file_id])
        for file_id in by_file
    }

    stage1_truth = [
        row.get("sample_type") == "technical_hard" for row in labels
    ]
    stage1_pred = [stage1[str(row["file"])] for row in labels]
    non_technical = [
        row for row in labels if row.get("sample_type") != "technical_hard"
    ]
    stage2_truth = [
        row.get("sample_type") == "semantic_defect" for row in non_technical
    ]
    semantic_intrinsic_pred = [semantic[str(row["file"])] for row in non_technical]
    reached_stage2 = [
        row for row in non_technical if not stage1[str(row["file"])]
    ]
    production_truth = [
        row.get("sample_type") == "semantic_defect" for row in reached_stage2
    ]
    production_pred = [semantic[str(row["file"])] for row in reached_stage2]
    final_truth = [row.get("sample_type") in DEFECT_TYPES for row in labels]
    final_pred = [final[str(row["file"])] for row in labels]

    errors = []
    for row in labels:
        file_id = str(row["file"])
        expected_reject = row.get("sample_type") in DEFECT_TYPES
        if final[file_id] != expected_reject:
            errors.append(
                {
                    "file": file_id,
                    "sample_type": row.get("sample_type"),
                    "expected_reject": expected_reject,
                    "stage1_reject": stage1[file_id],
                    "semantic_reject": semantic[file_id],
                    "stage2_reject": semantic[file_id],
                    "final_reject": final[file_id],
                    "error_type": "false_negative" if expected_reject else "false_positive",
                    "error_category": (
                        "stage1_feature"
                        if expected_reject
                        and row.get("sample_type") == "technical_hard"
                        else "semantic_gate_vlm"
                        if expected_reject
                        else "stage1_threshold"
                        if stage1[file_id]
                        else "semantic_gate_schema"
                    ),
                }
            )

    return {
        "stage1": {
            **binary_metrics(stage1_truth, stage1_pred),
            "class_rejection": _class_rejection(labels, stage1),
            "reason_recall": _reason_recall(labels, stage1, "technical_hard"),
        },
        "semantic_gate": {
            **binary_metrics(stage2_truth, semantic_intrinsic_pred),
            "class_rejection": _class_rejection(labels, semantic),
            "reason_recall": _reason_recall(labels, semantic, "semantic_defect"),
        },
        "semantic_gate_production": {
            **binary_metrics(production_truth, production_pred),
            "reached_semantic_gate": len(reached_stage2),
        },
        "pipeline": {
            **binary_metrics(final_truth, final_pred),
            "class_rejection": _class_rejection(labels, final),
            "technical_recall": _class_rejection(labels, final)["technical_hard"][
                "rate"
            ],
            "semantic_recall": _class_rejection(labels, final)["semantic_defect"][
                "rate"
            ],
        },
        "errors": errors,
    }


def build_fixed_packs(
    labels: list[dict[str, Any]],
    sessions: dict[str, str],
    *,
    pack_count: int = 10,
    seed: int = 20260810,
) -> list[dict[str, Any]]:
    """Partition all labels into deterministic, category-balanced packs."""
    quotas = {
        "technical_hard": 5,
        "semantic_defect": 5,
        "ordinary": 10,
        "highlight": 5,
    }
    expected = {category: pack_count * quota for category, quota in quotas.items()}
    observed = Counter(str(row.get("sample_type") or "") for row in labels)
    if dict(observed) != expected:
        raise ValueError(f"labels do not match pack quotas: {dict(observed)}")

    rng = random.Random(seed)
    packs = [
        {
            "id": f"pack_{index + 1:02d}",
            "files": [],
            "counts": Counter(),
            "sessions": Counter(),
        }
        for index in range(pack_count)
    ]
    for category in CATEGORY_ORDER:
        rows = [row for row in labels if row.get("sample_type") == category]
        rng.shuffle(rows)
        for row in rows:
            file_id = str(row["file"])
            session = sessions.get(file_id, "")
            eligible = [
                pack for pack in packs if pack["counts"][category] < quotas[category]
            ]
            minimum = min(
                eligible,
                key=lambda pack: (
                    pack["sessions"][session],
                    len(pack["files"]),
                    pack["id"],
                ),
            )
            minimum["files"].append(file_id)
            minimum["counts"][category] += 1
            minimum["sessions"][session] += 1

    return [
        {
            "id": pack["id"],
            "files": pack["files"],
            "counts": dict(pack["counts"]),
        }
        for pack in packs
    ]


def score_ranked_selection(
    selected: list[str],
    *,
    defects: set[str],
    acceptable: set[str],
    k: int,
) -> dict[str, Any]:
    unique = list(dict.fromkeys(str(file_id) for file_id in selected))
    top = unique[:k]
    violations = [file_id for file_id in top if file_id in defects]
    hits = [file_id for file_id in top if file_id in acceptable]
    return {
        "k": k,
        "selected_ids": top,
        "selected_count": len(top),
        "pool_smaller_than_k": len(top) < k,
        "defect_count": len(violations),
        "defect_ids": violations,
        "zero_blunder_passed": len(top) == k and not violations,
        "overlap_count": len(hits),
        "overlap_at_k": round(len(hits) / k, 6),
        "acceptable_ids": hits,
    }


def evaluate_agent_selection(
    selected: list[str],
    *,
    allowed_files: set[str],
    defects: set[str],
    acceptable: set[str],
    k: int,
) -> dict[str, Any]:
    """Validate Agent file-ID protocol, then score the valid ordered selection."""
    raw = [str(file_id) for file_id in selected]
    duplicates = [
        file_id
        for file_id, count in Counter(raw).items()
        if count > 1
    ]
    hallucinations = list(dict.fromkeys(
        file_id for file_id in raw if file_id not in allowed_files
    ))
    valid = [
        file_id
        for file_id in dict.fromkeys(raw)
        if file_id in allowed_files
    ]
    selection = score_ranked_selection(
        valid,
        defects=defects,
        acceptable=acceptable,
        k=k,
    )
    return {
        "raw_count": len(raw),
        "empty": not raw,
        "over_k": len(raw) > k,
        "duplicate_ids": duplicates,
        "hallucinated_ids": hallucinations,
        "protocol_passed": (
            bool(raw)
            and len(raw) <= k
            and not duplicates
            and not hallucinations
        ),
        "selection": selection,
    }
