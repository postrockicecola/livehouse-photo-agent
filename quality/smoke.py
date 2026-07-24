"""CI smoke for the quality platform (offline, no VLM).

Runs:
  1. ``validate_contracts`` on schema examples
  2. hydrate smoke fixture → golden_item.v1
  3. build version_manifest + stage3_scoring suite → eval_run.v1
  4. self-diff (current vs itself) to exercise diff writer

Usage::

    python -m quality.smoke
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "quality" / "fixtures" / "smoke"


def main() -> int:
    # Ensure repo root imports resolve when invoked as ``python -m quality.smoke``.
    if str(_REPO) not in sys.path:
        sys.path.insert(0, str(_REPO))

    from quality.dataset import hydrate_golden_items
    from quality.eval_run import emit_from_stage3_report, item_scores_from_joined
    from quality.manifest import build_version_manifest
    from quality.validate_contracts import main as validate_main
    from scripts.eval.labels import join_labels_predictions, load_labels, load_predictions
    from scripts.eval_stage3 import build_report

    print("== quality smoke: validate_contracts ==")
    rc = validate_main(["validate_contracts"])
    if rc != 0:
        return rc

    labels = _FIXTURE / "labels.jsonl"
    manifest = _FIXTURE / "manifest.json"
    preds = _FIXTURE / "predictions.json"
    cfg = _REPO / "configs" / "eval_stage3.yaml"
    if not cfg.is_file():
        print(f"error: missing {cfg}", file=sys.stderr)
        return 2

    print("== quality smoke: hydrate golden_item.v1 ==")
    items, errors = hydrate_golden_items(
        labels,
        manifest,
        default_splits=["core"],
        smoke_limit=8,
    )
    if errors:
        print(f"hydrate warnings: {errors}", file=sys.stderr)
    if len(items) < 4:
        print(f"error: expected ≥4 golden items, got {len(items)}", file=sys.stderr)
        return 1
    from quality.validate_contracts import validate_document

    for i, item in enumerate(items):
        verrs = validate_document(item, f"smoke_item[{i}]")
        if verrs:
            print("golden validation failed:", verrs[:5], file=sys.stderr)
            return 1
    print(f"hydrated {len(items)} items (smoke split tagged)")

    print("== quality smoke: version_manifest + stage3_scoring ==")
    vm = build_version_manifest(
        config_path=cfg,
        labels_path=labels,
        dataset_manifest_path=manifest,
        dataset_name="smoke_fixture",
        dataset_version="0.1.0",
        manifest_id="ci_smoke_stage3_scoring",
        created_at="2026-07-24T00:00:00+00:00",
        tags=["ci", "smoke"],
    )
    print(f"version_manifest_hash={vm['version_manifest_hash']}")
    print(f"prompt.content_hash={vm['prompt']['content_hash']}")
    print(f"workflow.config_hash={vm['workflow']['config_hash']}")

    lab = load_labels(labels)
    pred = load_predictions(preds)
    joined = join_labels_predictions(lab, pred)
    if joined.n_matched < 4:
        print(f"error: matched={joined.n_matched}", file=sys.stderr)
        return 1
    report = build_report(joined, [3, 5])
    from scripts.eval.protocol import stamp_protocol

    stamp_protocol(
        report,
        labels_path=labels,
        predictions_path=preds,
        config_path=cfg,
        attach_manifest=False,
    )
    # Attach the pinned manifest we just built (stable created_at).
    from quality.manifest import stamp_report_with_manifest

    stamp_report_with_manifest(report, vm)

    scores = item_scores_from_joined(joined.pairs)
    with tempfile.TemporaryDirectory(prefix="lk_quality_smoke_") as tmp:
        root = Path(tmp) / "runs"
        run, diff = emit_from_stage3_report(
            report,
            artifact_root=root,
            version_manifest=vm,
            dataset_name="smoke_fixture",
            dataset_version="0.1.0",
            suite="stage3_scoring",
            item_scores=scores,
            tags=["ci", "smoke"],
            split_filter=["smoke"],
        )
        # Second emit with baseline = first run (metric deltas ~0).
        run2, diff2 = emit_from_stage3_report(
            report,
            artifact_root=root,
            version_manifest=vm,
            dataset_name="smoke_fixture",
            dataset_version="0.1.0",
            suite="stage3_scoring",
            baseline_path=run["artifact_root"],
            item_scores=scores,
            tags=["ci", "smoke", "vs_baseline"],
            split_filter=["smoke"],
        )
        assert diff2 is not None
        spearman = (run.get("metrics") or {}).get("spearman_overall")
        print(f"eval_run_id={run['eval_run_id']} spearman_overall={spearman}")
        print(f"baseline_diff_metrics={len(diff2.get('metric_deltas') or [])}")
        # Gate: fixture is constructed to correlate — require finite spearman.
        if spearman is None:
            print("error: spearman_overall missing", file=sys.stderr)
            return 1
        gate_path = Path(run2["artifact_root"]) / "run.json"
        print(f"OK smoke artifacts under {gate_path.parent}")

    # Optional: hydrate a sample of the real golden_core (contract readiness).
    real_labels = _REPO / "data" / "eval" / "labels.jsonl"
    real_manifest = _REPO / "data" / "eval" / "manifest.json"
    if real_labels.is_file() and real_manifest.is_file():
        print("== quality smoke: golden_core hydrate sample ==")
        real_items, real_errs = hydrate_golden_items(
            real_labels,
            real_manifest,
            smoke_limit=16,
            skip_missing_hash=True,
        )
        bad = 0
        for i, item in enumerate(real_items[:32]):
            verrs = validate_document(item, f"golden_core[{i}]")
            if verrs:
                bad += 1
                if bad <= 3:
                    print(verrs[:3], file=sys.stderr)
        print(
            f"golden_core hydrated={len(real_items)} "
            f"sample_invalid={bad} skip_errors={len(real_errs)}"
        )
        if bad:
            return 1

    print("quality smoke: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
