#!/usr/bin/env python3
"""Fit and evaluate local pack-level reranker baselines."""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.diversity_selector import apply_diversity_selection, diversity_settings

K = 5
RISK_GRID = (0.0, 2.0, 4.0, 6.0)
FUSION_ALPHA_GRID = tuple(index / 10 for index in range(16))
FUSION_PENALTY_GRID = tuple(index / 10 for index in range(11))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _prediction_rows(path: Path) -> list[dict[str, Any]]:
    raw = _read_json(path)
    if isinstance(raw, dict):
        raw = raw.get("results") or []
    return [row for row in raw if isinstance(row, dict)]


def _file_id(row: dict[str, Any]) -> str:
    return str(row.get("file") or row.get("file_name") or row.get("image") or "")


def _overall(row: dict[str, Any]) -> float:
    return float(row.get("overall_score") or (row.get("scores") or {}).get("overall") or 0)


def _risk_score(row: dict[str, Any], weights: tuple[float, float, float]) -> float:
    dims = row.get("dimensions") or {}
    composition = float(dims.get("composition_framing") or 0)
    subject = float(dims.get("deliverable_subject") or 0)
    focus = float(dims.get("focus_sharpness") or 0)
    composition_w, subject_w, focus_w = weights
    penalty = (
        composition_w * max(0.0, 8.0 - composition)
        + subject_w * max(0.0, 8.0 - subject)
        + focus_w * max(0.0, 7.5 - focus)
    )
    return _overall(row) - penalty


def _rank(
    pack: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], float],
    *,
    diverse: bool,
) -> list[str]:
    files = list(pack["files"])
    rows = [dict(predictions[file_id]) for file_id in files]
    if not diverse:
        return sorted(files, key=lambda file_id: (-score_fn(predictions[file_id]), file_id))[:K]

    settings = diversity_settings(
        {"processing": {"diversity_selection": {"clip_on_demand": False}}}
    )
    reps, members_by_rep, _ = apply_diversity_selection(
        rows,
        settings,
        order_key_fn=score_fn,
    )
    ranked_indices = list(reps)
    if len(ranked_indices) < K:
        folded = {
            index for members in members_by_rep.values() for index in members
        }
        ranked_indices.extend(
            sorted(folded, key=lambda index: score_fn(rows[index]), reverse=True)
        )
    return [files[index] for index in ranked_indices[:K]]


def _ndcg_at_five(predicted: list[str], human_order: list[str]) -> float:
    relevance = {file_id: K - index for index, file_id in enumerate(human_order[:K])}

    def dcg(order: list[str]) -> float:
        return sum(
            relevance.get(file_id, 0) / math.log2(index + 2)
            for index, file_id in enumerate(order[:K])
        )

    ideal = dcg(human_order)
    return dcg(predicted) / ideal if ideal else 0.0


def _score_pack(
    predicted: list[str],
    human_order: list[str],
    *,
    excluded_ids: set[str] | None = None,
    duplicate_ids: set[str] | None = None,
) -> dict[str, Any]:
    hits = [file_id for file_id in predicted[:K] if file_id in set(human_order[:K])]
    excluded_hits = [
        file_id for file_id in predicted[:K] if file_id in (excluded_ids or set())
    ]
    duplicate_hits = [
        file_id for file_id in predicted[:K] if file_id in (duplicate_ids or set())
    ]
    return {
        "selected_ids": predicted[:K],
        "overlap_count": len(hits),
        "overlap_at_5": len(hits) / K,
        "ndcg_at_5": _ndcg_at_five(predicted, human_order),
        "top1_match": bool(predicted and human_order and predicted[0] == human_order[0]),
        "excluded_count": len(excluded_hits),
        "excluded_ids": excluded_hits,
        "duplicate_count": len(duplicate_hits),
        "duplicate_ids": duplicate_hits,
        "zero_blunder": not excluded_hits and not duplicate_hits,
    }


def _evaluate_arm(
    packs: list[dict[str, Any]],
    reviews: dict[str, dict[str, Any]],
    rank_fn: Callable[[dict[str, Any]], list[str]],
) -> dict[str, Any]:
    cases = []
    for pack in packs:
        review = reviews[pack["id"]]
        scored = _score_pack(
            rank_fn(pack),
            review["selected_ids"],
            excluded_ids=set(review.get("excluded_ids") or []),
            duplicate_ids=set(review.get("duplicate_ids") or []),
        )
        cases.append(
            {
                "pack_id": pack["id"],
                "session": pack["session"],
                **scored,
            }
        )
    count = len(cases)
    return {
        "pack_count": count,
        "macro_overlap_at_5": sum(case["overlap_at_5"] for case in cases) / count,
        "macro_ndcg_at_5": sum(case["ndcg_at_5"] for case in cases) / count,
        "top1_accuracy": sum(case["top1_match"] for case in cases) / count,
        "exact_top5_set_rate": sum(case["overlap_count"] == K for case in cases) / count,
        "selected_excluded_rate": (
            sum(case["excluded_count"] for case in cases) / (count * K)
        ),
        "selected_duplicate_rate": (
            sum(case["duplicate_count"] for case in cases) / (count * K)
        ),
        "zero_blunder_pack_rate": sum(case["zero_blunder"] for case in cases) / count,
        "cases": cases,
    }


def fit_risk_weights(
    development_packs: list[dict[str, Any]],
    reviews: dict[str, dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
) -> tuple[float, float, float]:
    candidates = itertools.product(RISK_GRID, repeat=3)

    def objective(weights: tuple[float, float, float]) -> tuple[float, float, float, float]:
        result = _evaluate_arm(
            development_packs,
            reviews,
            lambda pack: _rank(
                pack,
                predictions,
                lambda row: _risk_score(row, weights),
                diverse=False,
            ),
        )
        return (
            result["zero_blunder_pack_rate"],
            result["macro_overlap_at_5"],
            result["macro_ndcg_at_5"],
            -sum(weights),
        )

    return max(candidates, key=objective)


def _rank_fusion(
    pack: dict[str, Any],
    predictions: dict[str, dict[str, Any]],
    comparative: dict[str, Any],
    *,
    alpha: float,
    penalty: float,
) -> list[str]:
    files = list(pack["files"])
    values = [_overall(predictions[file_id]) for file_id in files]
    low, high = min(values), max(values)
    span = high - low or 1.0
    raw = {
        file_id: (_overall(predictions[file_id]) - low) / span for file_id in files
    }
    vlm_rank = {
        file_id: (K - index) / K
        for index, file_id in enumerate(comparative.get("ranked_top5") or [])
    }
    flagged = set(comparative.get("must_exclude") or []) | set(
        comparative.get("weaker_duplicates") or []
    )
    return sorted(
        files,
        key=lambda file_id: (
            -(
                raw[file_id]
                + alpha * vlm_rank.get(file_id, 0.0)
                - penalty * (file_id in flagged)
            ),
            file_id,
        ),
    )[:K]


def fit_fusion(
    development_packs: list[dict[str, Any]],
    reviews: dict[str, dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
    comparative: dict[str, dict[str, Any]],
) -> tuple[float, float]:
    candidates = itertools.product(FUSION_ALPHA_GRID, FUSION_PENALTY_GRID)

    def objective(params: tuple[float, float]) -> tuple[float, float, float, float]:
        alpha, penalty = params
        result = _evaluate_arm(
            development_packs,
            reviews,
            lambda pack: _rank_fusion(
                pack,
                predictions,
                comparative[pack["id"]],
                alpha=alpha,
                penalty=penalty,
            ),
        )
        return (
            result["zero_blunder_pack_rate"],
            result["macro_overlap_at_5"],
            result["macro_ndcg_at_5"],
            -(alpha + penalty),
        )

    return max(candidates, key=objective)


def evaluate(
    *,
    manifest_path: Path,
    reviews_path: Path,
    predictions_path: Path,
    comparative_vlm_path: Path | None = None,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    packs = [pack for pack in manifest.get("packs") or [] if isinstance(pack, dict)]
    reviews = {row["pack_id"]: row for row in _read_jsonl(reviews_path)}
    predictions = {
        _file_id(row): row for row in _prediction_rows(predictions_path) if _file_id(row)
    }
    missing_reviews = [pack["id"] for pack in packs if pack["id"] not in reviews]
    missing_predictions = [
        file_id
        for pack in packs
        for file_id in pack["files"]
        if file_id not in predictions
    ]
    if missing_reviews or missing_predictions:
        raise ValueError(
            f"missing reviews={missing_reviews[:5]} predictions={missing_predictions[:5]}"
        )

    development = [pack for pack in packs if pack["split"] == "development"]
    holdout = [pack for pack in packs if pack["split"] == "holdout"]
    weights = fit_risk_weights(development, reviews, predictions)

    def arms(eval_packs: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "raw": _evaluate_arm(
                eval_packs,
                reviews,
                lambda pack: _rank(pack, predictions, _overall, diverse=False),
            ),
            "diversity": _evaluate_arm(
                eval_packs,
                reviews,
                lambda pack: _rank(pack, predictions, _overall, diverse=True),
            ),
            "risk": _evaluate_arm(
                eval_packs,
                reviews,
                lambda pack: _rank(
                    pack,
                    predictions,
                    lambda row: _risk_score(row, weights),
                    diverse=False,
                ),
            ),
            "risk_diversity": _evaluate_arm(
                eval_packs,
                reviews,
                lambda pack: _rank(
                    pack,
                    predictions,
                    lambda row: _risk_score(row, weights),
                    diverse=True,
                ),
            ),
        }

    report = {
        "schema_version": "pack_reranker_eval.v1",
        "k": K,
        "fit_protocol": "risk weights fitted on development only; holdout untouched",
        "risk_formula": (
            "overall - wc*max(0,8-composition) - ws*max(0,8-subject) "
            "- wf*max(0,7.5-focus)"
        ),
        "risk_grid": list(RISK_GRID),
        "fitted_risk_weights": {
            "composition": weights[0],
            "deliverable_subject": weights[1],
            "focus_sharpness": weights[2],
        },
        "development": arms(development),
        "holdout": arms(holdout),
    }
    if comparative_vlm_path is not None:
        comparative = {
            row["pack_id"]: row
            for row in _read_jsonl(comparative_vlm_path)
            if row.get("status") == "success"
        }
        missing_comparative = [
            pack["id"] for pack in packs if pack["id"] not in comparative
        ]
        if missing_comparative:
            raise ValueError(f"missing comparative VLM results={missing_comparative[:5]}")
        alpha, penalty = fit_fusion(
            development,
            reviews,
            predictions,
            comparative,
        )

        def comparative_arms(eval_packs: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "comparative_vlm": _evaluate_arm(
                    eval_packs,
                    reviews,
                    lambda pack: list(comparative[pack["id"]]["ranked_top5"])[:K],
                ),
                "raw_vlm_fusion": _evaluate_arm(
                    eval_packs,
                    reviews,
                    lambda pack: _rank_fusion(
                        pack,
                        predictions,
                        comparative[pack["id"]],
                        alpha=alpha,
                        penalty=penalty,
                    ),
                ),
            }

        report["fusion_protocol"] = (
            "alpha and VLM exclusion penalty fitted on development only"
        )
        report["fitted_fusion"] = {
            "vlm_rank_alpha": alpha,
            "vlm_flag_penalty": penalty,
        }
        report["development"].update(comparative_arms(development))
        report["holdout"].update(comparative_arms(holdout))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packs", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--comparative-vlm", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(
        manifest_path=args.packs,
        reviews_path=args.reviews,
        predictions_path=args.predictions,
        comparative_vlm_path=args.comparative_vlm,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = {
        "fitted_risk_weights": report["fitted_risk_weights"],
        "development": {
            name: {
                key: value
                for key, value in result.items()
                if key != "cases"
            }
            for name, result in report["development"].items()
        },
        "holdout": {
            name: {
                key: value
                for key, value in result.items()
                if key != "cases"
            }
            for name, result in report["holdout"].items()
        },
        "output": str(args.output),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
