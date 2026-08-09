"""Emit ``eval_run.v1`` bundles and minimal baseline diffs.

Artifact layout (under ``artifact_root``)::

    run.json
    version_manifest.json
    metrics.json
    report.json          # full stage3 report (optional mirror)
    diff.json            # when baseline_run_id / baseline path provided
"""
from __future__ import annotations

import argparse
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from quality.manifest import (
    DEFAULT_DATASET_NAME,
    DEFAULT_DATASET_VERSION,
    compact_manifest_ref,
    write_version_manifest,
)
from quality.validate_contracts import validate_document

_REPO = Path(__file__).resolve().parents[1]


def new_eval_run_id() -> str:
    """Opaque ``evr_`` + 26 hex chars (UUID-derived, ULID-shaped length)."""
    return "evr_" + uuid.uuid4().hex[:26]


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return v


def metrics_from_stage3_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Map ``scripts.eval_stage3.build_report`` output → ``metrics.stage3_scoring.v1``."""
    overall = report.get("overall") if isinstance(report.get("overall"), dict) else {}
    per_dim = report.get("per_dimension") if isinstance(report.get("per_dimension"), dict) else {}
    selection = report.get("selection") if isinstance(report.get("selection"), dict) else {}
    return {
        "schema": "metrics.stage3_scoring.v1",
        "spearman_overall": _num(overall.get("spearman")),
        "pearson_overall": _num(overall.get("pearson")),
        "mae_overall": _num(overall.get("mae")),
        "rmse_overall": _num(overall.get("rmse")),
        "n_scored": int(overall.get("n") or 0),
        "per_dim": per_dim,
        "selection": {
            "n": selection.get("n"),
            "n_positives": selection.get("n_positives"),
            "at_k": selection.get("at_k"),
            "score_gap": _num(selection.get("score_gap")),
        },
        "macro_dim_mae": _num(report.get("macro_dim_mae")),
        "parse_fail_rate": _num(report.get("parse_fail_rate")) or 0.0,
    }


def build_eval_run(
    *,
    suite: str,
    metrics: Mapping[str, Any],
    version_manifest: Mapping[str, Any],
    dataset: Mapping[str, Any],
    artifact_root: str | Path,
    eval_run_id: str | None = None,
    status: str = "succeeded",
    started_at: str | None = None,
    finished_at: str | None = None,
    baseline_run_id: str | None = None,
    protocol: Mapping[str, Any] | None = None,
    counts: Mapping[str, Any] | None = None,
    gate: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
    error: str | None = None,
    compact_manifest: bool = True,
) -> dict[str, Any]:
    """Assemble a validated ``eval_run.v1`` document."""
    now = datetime.now(timezone.utc).isoformat()
    vm: Any
    if compact_manifest:
        vm = compact_manifest_ref(version_manifest)
    else:
        vm = dict(version_manifest)
    run: dict[str, Any] = {
        "schema_version": "eval_run.v1",
        "eval_run_id": eval_run_id or new_eval_run_id(),
        "suite": suite,
        "status": status,
        "started_at": started_at or now,
        "finished_at": finished_at or now,
        "version_manifest": vm,
        "dataset": dict(dataset),
        "metrics": dict(metrics),
        "artifact_root": str(Path(artifact_root).as_posix()),
        "baseline_run_id": baseline_run_id,
    }
    if protocol:
        run["protocol"] = dict(protocol)
    if counts:
        run["counts"] = dict(counts)
    if gate:
        run["gate"] = dict(gate)
    if tags:
        run["tags"] = list(tags)
    if error:
        run["error"] = error
    return run


def _metric_leafs(metrics: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in metrics.items():
        if k == "schema":
            continue
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            # Skip bulky nested tables except known scalar nests.
            if k == "selection":
                gap = _num(v.get("score_gap"))
                if gap is not None:
                    out[f"{path}.score_gap"] = gap
                for row in v.get("at_k") or []:
                    if isinstance(row, dict) and "k" in row:
                        pk = _num(row.get("precision"))
                        rk = _num(row.get("recall"))
                        if pk is not None:
                            out[f"{path}.precision@{row['k']}"] = pk
                        if rk is not None:
                            out[f"{path}.recall@{row['k']}"] = rk
                continue
            if k == "per_dim":
                for dim, stats in v.items():
                    if isinstance(stats, dict):
                        mae = _num(stats.get("mae"))
                        sp = _num(stats.get("spearman"))
                        if mae is not None:
                            out[f"per_dim.{dim}.mae"] = mae
                        if sp is not None:
                            out[f"per_dim.{dim}.spearman"] = sp
                continue
            continue
        num = _num(v)
        if num is not None:
            out[path] = num
    return out


def diff_eval_runs(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    top_n: int = 12,
) -> dict[str, Any]:
    """Minimal run diff: metric deltas (+ optional per-file regressors)."""
    b_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    c_metrics = current.get("metrics") if isinstance(current.get("metrics"), dict) else {}
    b_leafs = _metric_leafs(b_metrics)
    c_leafs = _metric_leafs(c_metrics)
    keys = sorted(set(b_leafs) | set(c_leafs))
    deltas: list[dict[str, Any]] = []
    for key in keys:
        b = b_leafs.get(key)
        c = c_leafs.get(key)
        if b is None and c is None:
            continue
        delta = None if b is None or c is None else c - b
        deltas.append({"metric": key, "baseline": b, "current": c, "delta": delta})

    # Prefer highlighting drops on "higher is better" rank metrics.
    def _sort_key(row: dict[str, Any]) -> float:
        d = row.get("delta")
        if d is None:
            return 0.0
        name = str(row.get("metric") or "")
        if "mae" in name or "rmse" in name or "parse_fail" in name:
            return -float(d)  # increase is bad → sort high
        return float(d)  # decrease is bad → sort low first via reverse

    regressions = sorted(
        [r for r in deltas if r.get("delta") is not None],
        key=_sort_key,
    )
    # For higher-is-better: most negative first; for error metrics already flipped.
    worst = regressions[:top_n]

    per_item: list[dict[str, Any]] = []
    b_pairs = baseline.get("item_scores") if isinstance(baseline.get("item_scores"), list) else []
    c_pairs = current.get("item_scores") if isinstance(current.get("item_scores"), list) else []
    if b_pairs and c_pairs:
        b_map = {
            str(r.get("file_key") or r.get("file")): r
            for r in b_pairs
            if isinstance(r, dict)
        }
        for row in c_pairs:
            if not isinstance(row, dict):
                continue
            key = str(row.get("file_key") or row.get("file") or "")
            prev = b_map.get(key)
            if not prev:
                continue
            b_err = _num(prev.get("abs_error"))
            c_err = _num(row.get("abs_error"))
            if b_err is None or c_err is None:
                continue
            per_item.append(
                {
                    "file_key": key,
                    "file": row.get("file") or prev.get("file"),
                    "baseline_abs_error": b_err,
                    "current_abs_error": c_err,
                    "delta_abs_error": c_err - b_err,
                    "label_overall": row.get("label_overall"),
                    "pred_overall": row.get("pred_overall"),
                }
            )
        per_item.sort(key=lambda r: float(r.get("delta_abs_error") or 0.0), reverse=True)
        per_item = per_item[:top_n]

    return {
        "schema": "eval_run_diff.v1",
        "baseline_run_id": baseline.get("eval_run_id"),
        "current_run_id": current.get("eval_run_id"),
        "metric_deltas": deltas,
        "worst_metric_moves": worst,
        "top_regressors": per_item,
    }


def item_scores_from_joined(pairs: list[tuple[Any, Any]]) -> list[dict[str, Any]]:
    """Build per-item score rows for diff (label/pred overall + abs error)."""
    rows: list[dict[str, Any]] = []
    for lb, pred in pairs:
        lo = getattr(lb, "overall", None)
        po = getattr(pred, "overall", None)
        if lo is None or po is None:
            continue
        rows.append(
            {
                "file": getattr(lb, "file", None) or getattr(pred, "file", None),
                "file_key": getattr(lb, "key", None) or getattr(pred, "key", None),
                "label_overall": float(lo),
                "pred_overall": float(po),
                "abs_error": abs(float(po) - float(lo)),
            }
        )
    return rows


def write_eval_run_bundle(
    artifact_root: str | Path,
    *,
    eval_run: Mapping[str, Any],
    version_manifest: Mapping[str, Any] | None = None,
    report: Mapping[str, Any] | None = None,
    diff: Mapping[str, Any] | None = None,
    item_scores: list[Mapping[str, Any]] | None = None,
) -> Path:
    """Write immutable-ish artifact directory; returns path to ``run.json``."""
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    run_path = root / "run.json"
    run_doc = dict(eval_run)
    run_doc["artifact_root"] = root.as_posix()
    if item_scores is not None:
        # Stored beside run for diffs; not part of eval_run.v1 schema.
        (root / "item_scores.jsonl").write_text(
            "".join(json.dumps(dict(r), ensure_ascii=False) + "\n" for r in item_scores),
            encoding="utf-8",
        )
    run_path.write_text(
        json.dumps(run_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    metrics = run_doc.get("metrics") or {}
    (root / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if version_manifest is not None:
        write_version_manifest(root / "version_manifest.json", version_manifest)
    if report is not None:
        (root / "report.json").write_text(
            json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if diff is not None:
        (root / "diff.json").write_text(
            json.dumps(dict(diff), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    errors = validate_document(run_doc, str(run_path))
    if errors:
        raise ValueError("eval_run validation failed: " + "; ".join(errors[:8]))
    return run_path


def load_eval_run(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if p.is_dir():
        p = p / "run.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: expected object")
    # Attach item_scores when sibling present (for richer diffs).
    sibling = p.parent / "item_scores.jsonl"
    if sibling.is_file() and "item_scores" not in doc:
        rows: list[dict[str, Any]] = []
        for line in sibling.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        doc["item_scores"] = rows
    return doc


def emit_from_stage3_report(
    report: Mapping[str, Any],
    *,
    artifact_root: str | Path,
    version_manifest: Mapping[str, Any],
    dataset_name: str = DEFAULT_DATASET_NAME,
    dataset_version: str = DEFAULT_DATASET_VERSION,
    suite: str = "stage3_scoring",
    baseline_path: str | Path | None = None,
    item_scores: list[Mapping[str, Any]] | None = None,
    tags: list[str] | None = None,
    split_filter: list[str] | None = None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """High-level helper used by ``eval_stage3`` and smoke CI."""
    metrics = metrics_from_stage3_report(report)
    ds = version_manifest.get("dataset") if isinstance(version_manifest.get("dataset"), dict) else {}
    dataset = {
        "name": ds.get("name") or dataset_name,
        "version": ds.get("version") or dataset_version,
    }
    if ds.get("labels_sha256"):
        dataset["labels_sha256"] = ds["labels_sha256"]
    if ds.get("manifest_sha256"):
        dataset["manifest_sha256"] = ds["manifest_sha256"]
    if split_filter:
        dataset["split_filter"] = list(split_filter)

    baseline_run_id = None
    baseline_doc = None
    if baseline_path:
        baseline_doc = load_eval_run(baseline_path)
        baseline_run_id = str(baseline_doc.get("eval_run_id") or "")

    run_id = new_eval_run_id()
    root = Path(artifact_root)
    # Callers usually pass a parent dir; nest under eval_run_id unless already there.
    if root.name != run_id:
        root = root / run_id

    counts = {
        "items_total": int(report.get("matched") or 0)
        + len(report.get("labels_unmatched") or []),
        "items_scored": int((report.get("overall") or {}).get("n") or 0),
        "items_matched": int(report.get("matched") or 0),
        "parse_fail": 0,
    }
    run = build_eval_run(
        suite=suite,
        metrics=metrics,
        version_manifest=version_manifest,
        dataset=dataset,
        artifact_root=root,
        eval_run_id=run_id,
        baseline_run_id=baseline_run_id or None,
        protocol=report.get("protocol") if isinstance(report.get("protocol"), dict) else None,
        counts=counts,
        tags=tags,
    )
    diff = None
    if baseline_doc is not None:
        current_for_diff = dict(run)
        if item_scores is not None:
            current_for_diff["item_scores"] = list(item_scores)
        diff = diff_eval_runs(baseline_doc, current_for_diff)

    write_eval_run_bundle(
        root,
        eval_run=run,
        version_manifest=version_manifest,
        report=report,
        diff=diff,
        item_scores=list(item_scores) if item_scores is not None else None,
    )
    # Refresh artifact_root on returned doc.
    run["artifact_root"] = root.as_posix()
    return run, diff


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diff two eval_run.v1 artifacts")
    parser.add_argument("baseline", help="path to baseline run.json or artifact dir")
    parser.add_argument("current", help="path to current run.json or artifact dir")
    parser.add_argument("--out", default=None, help="optional diff.json path")
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args(argv)
    baseline = load_eval_run(args.baseline)
    current = load_eval_run(args.current)
    diff = diff_eval_runs(baseline, current, top_n=args.top)
    text = json.dumps(diff, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    worst = diff.get("worst_metric_moves") or []
    if worst:
        print("\nTop metric moves:", file=__import__("sys").stderr)
        for row in worst[:5]:
            print(
                f"  {row['metric']}: {row.get('baseline')} → {row.get('current')} "
                f"(Δ={row.get('delta')})",
                file=__import__("sys").stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
