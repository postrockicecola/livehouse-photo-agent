"""Prompt A/B splits and decision rules. Spearman is diagnostic only."""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any, Iterable, Mapping

from scripts.eval.labels import (
    Label,
    Prediction,
    join_labels_predictions,
    load_labels,
    load_predictions,
    normalize_name,
)
from scripts.eval_stage3 import build_report
from quality.validity import evaluate_validity_gate

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPLITS = ROOT / "data" / "eval" / "selection_v1" / "prompt_ab_splits.json"
HOLDOUT_N = 50
CANARY_N = 16
HOLDOUT_QUOTAS = {
    "technical_hard": 10,
    "semantic_defect": 10,
    "ordinary": 20,
    "highlight": 10,
}
CANARY_QUOTAS = {
    "technical_hard": 4,
    "semantic_defect": 4,
    "ordinary": 4,
    "highlight": 4,
}
VALID_SPLITS = frozenset({"canary", "open", "holdout"})
DECISION_K = (5, 10)


class HoldoutSealedError(RuntimeError):
    """Raised when a prompt-iteration run tries to use the sealed holdout."""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_splits(
    items: Iterable[Mapping[str, Any]],
    *,
    seed: int = 20260817,
) -> dict[str, Any]:
    by_type: dict[str, list[str]] = {key: [] for key in HOLDOUT_QUOTAS}
    for item in items:
        file_id = str(item.get("file") or "")
        sample_type = str(item.get("sample_type") or "")
        if file_id and sample_type in by_type:
            by_type[sample_type].append(file_id)
    rng = random.Random(seed)
    holdout: list[str] = []
    open_pool: dict[str, list[str]] = {}
    for sample_type, quota in HOLDOUT_QUOTAS.items():
        pool = list(by_type[sample_type])
        rng.shuffle(pool)
        holdout.extend(pool[:quota])
        open_pool[sample_type] = pool[quota:]
    canary: list[str] = []
    for sample_type, quota in CANARY_QUOTAS.items():
        pool = list(open_pool[sample_type])
        rng.shuffle(pool)
        canary.extend(pool[:quota])
    holdout_set = set(holdout)
    canary_set = set(canary)
    all_files = [str(item.get("file") or "") for item in items if item.get("file")]
    open_files = [name for name in all_files if name not in holdout_set]
    return {
        "schema_version": "prompt_ab_splits.v1",
        "seed": seed,
        "policy": {
            "holdout": "sealed; prompt iteration must not inspect failures",
            "canary": "cheap A/B on open only",
            "open": "all non-holdout frames",
            "decision_metrics": [
                "validity",
                "human_keep_precision_at_k",
                "zero_blunder",
            ],
            "diagnostics_only": ["spearman", "mae", "semantic_gate_recall"],
        },
        "holdout": sorted(holdout),
        "canary": sorted(canary),
        "open": sorted(open_files),
        "counts": {
            "holdout": len(holdout_set),
            "canary": len(canary_set),
            "open": len(open_files),
        },
    }


def write_splits(dataset_dir: Path, *, seed: int = 20260817) -> dict[str, Any]:
    frozen = _read_json(dataset_dir / "frozen_manifest.json")
    splits = build_splits(frozen.get("items") or [], seed=seed)
    out = dataset_dir / "prompt_ab_splits.json"
    out.write_text(json.dumps(splits, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return splits


def load_splits(path: Path | None = None) -> dict[str, Any]:
    splits_path = path or DEFAULT_SPLITS
    if not splits_path.is_file():
        raise FileNotFoundError(splits_path)
    return _read_json(splits_path)


def files_for_split(splits: Mapping[str, Any], split: str) -> list[str]:
    if split not in VALID_SPLITS:
        raise ValueError(f"split must be one of {sorted(VALID_SPLITS)}")
    return [str(name) for name in (splits.get(split) or [])]


def require_split(split: str, *, accept_holdout: bool) -> None:
    if split == "holdout" and not accept_holdout:
        raise HoldoutSealedError(
            "holdout is sealed; pass --accept-holdout only for a one-shot final accept"
        )


def _keyset(files: Iterable[str]) -> set[str]:
    return {normalize_name(name) for name in files if name}


def filter_labels(labels: Iterable[Label], files: Iterable[str]) -> list[Label]:
    allowed = _keyset(files)
    return [row for row in labels if row.key in allowed]


def filter_predictions(preds: Iterable[Prediction], files: Iterable[str]) -> list[Prediction]:
    allowed = _keyset(files)
    return [row for row in preds if row.key in allowed]


def _at_k(selection: Mapping[str, Any], k: int) -> float | None:
    for row in selection.get("at_k") or []:
        if int(row.get("k") or 0) == k:
            value = row.get("precision")
            return None if value is None else float(value)
    return None


def zero_blunder(pairs: list[tuple[Label, Prediction]], *, k: int) -> dict[str, Any]:
    ranked = [
        (lb, pred)
        for lb, pred in pairs
        if pred.overall is not None and lb.keep is not None
    ]
    ranked.sort(key=lambda item: float(item[1].overall or 0.0), reverse=True)
    top = ranked[:k]
    defects = [lb.file for lb, _pred in top if lb.keep is False]
    return {
        "k": k,
        "n_ranked": len(ranked),
        "defect_count": len(defects),
        "passed": len(defects) == 0 and len(top) > 0,
        "defect_files": defects,
    }


def score_predictions_on_split(
    *,
    labels: list[Label],
    predictions: list[Prediction],
    files: list[str],
    topks: Iterable[int] = DECISION_K,
) -> dict[str, Any]:
    joined = join_labels_predictions(filter_labels(labels, files), filter_predictions(predictions, files))
    topk_list = list(topks)
    report = build_report(joined, topk_list)
    blunders = {str(k): zero_blunder(joined.pairs, k=k) for k in topk_list}
    selection = report.get("selection") or {}
    return {
        "matched": joined.n_matched,
        "validity": report.get("validity") or {},
        "human_keep": {
            "n": selection.get("n"),
            "n_positives": selection.get("n_positives"),
            "score_gap": selection.get("score_gap"),
            "precision_at_5": _at_k(selection, 5),
            "precision_at_10": _at_k(selection, 10),
            "at_k": selection.get("at_k"),
        },
        "zero_blunder": blunders,
        "diagnostics": {
            "spearman": (report.get("overall") or {}).get("spearman"),
            "mae": (report.get("overall") or {}).get("mae"),
            "n_scored_overall": (report.get("overall") or {}).get("n"),
        },
    }


def _better_or_equal(candidate: float | None, baseline: float | None, *, higher: bool) -> bool:
    if candidate is None or baseline is None:
        return True
    return candidate >= baseline if higher else candidate <= baseline


def decide(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Win/loss: Validity + human_keep P@K + Zero Blunder. Spearman is ignored."""
    checks: list[dict[str, Any]] = []
    b_val = baseline.get("validity") or {}
    c_val = candidate.get("validity") or {}
    validity_gate = evaluate_validity_gate(c_val, baseline=b_val)
    checks.extend(validity_gate.get("checks") or [])

    for key, higher in (
        ("precision_at_5", True),
        ("precision_at_10", True),
    ):
        b = (baseline.get("human_keep") or {}).get(key)
        c = (candidate.get("human_keep") or {}).get(key)
        ok = _better_or_equal(c, b, higher=higher)
        checks.append(
            {
                "id": f"human_keep_{key}",
                "result": "pass" if ok else "fail",
                "metric": key,
                "actual": c,
                "threshold": b,
                "message": f"{key} {c} vs baseline {b}",
            }
        )

    for k in DECISION_K:
        b_map = baseline.get("zero_blunder") or {}
        c_map = candidate.get("zero_blunder") or {}
        b_row = b_map.get(k) or b_map.get(str(k)) or {}
        c_row = c_map.get(k) or c_map.get(str(k)) or {}
        b_n = b_row.get("defect_count")
        c_n = c_row.get("defect_count")
        ok = _better_or_equal(
            None if c_n is None else float(c_n),
            None if b_n is None else float(b_n),
            higher=False,
        )
        checks.append(
            {
                "id": f"zero_blunder@{k}",
                "result": "pass" if ok else "fail",
                "metric": f"zero_blunder_defects@{k}",
                "actual": c_n,
                "threshold": b_n,
                "message": f"defects@{k} {c_n} vs baseline {b_n}",
            }
        )

    failed = any(row.get("result") == "fail" for row in checks)
    return {
        "name": "prompt_ab",
        "result": "fail" if failed else "pass",
        "winner": "baseline" if failed else "candidate",
        "checks": checks,
        "diagnostics_ignored": ["spearman", "mae", "semantic_gate_recall"],
    }


def compare_prediction_files(
    *,
    baseline_path: Path,
    candidate_path: Path,
    labels_path: Path,
    splits_path: Path,
    split: str,
    accept_holdout: bool = False,
) -> dict[str, Any]:
    require_split(split, accept_holdout=accept_holdout)
    splits = load_splits(splits_path)
    files = files_for_split(splits, split)
    labels = load_labels(labels_path)
    baseline = score_predictions_on_split(
        labels=labels,
        predictions=load_predictions(baseline_path),
        files=files,
    )
    candidate = score_predictions_on_split(
        labels=labels,
        predictions=load_predictions(candidate_path),
        files=files,
    )
    return {
        "schema_version": "prompt_ab_report.v1",
        "split": split,
        "n_files": len(files),
        "baseline_predictions": str(baseline_path),
        "candidate_predictions": str(candidate_path),
        "baseline": baseline,
        "candidate": candidate,
        "decision": decide(baseline, candidate),
    }
