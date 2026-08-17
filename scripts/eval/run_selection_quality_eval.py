#!/usr/bin/env python3
"""Prepare and score the frozen selection_v1 quality benchmark."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_eval.scorers.pipeline_quality import (  # noqa: E402
    build_fixed_packs,
    evaluate_pipeline,
    score_ranked_selection,
)
from scripts.eval.freeze_selection_dataset import freeze_dataset  # noqa: E402
from scripts.eval.metrics import bias_stats, mae, rmse, spearman  # noqa: E402
from services.processor.pipeline_image_ops import assess_stage1_opencv  # noqa: E402
from utils.config_loader import ConfigLoader  # noqa: E402


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def load_config(path: Path) -> tuple[dict[str, Any], Path]:
    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    dataset = config.get("dataset") or {}
    dataset_dir = Path(str(dataset.get("directory") or ""))
    if not dataset_dir.is_absolute():
        dataset_dir = ROOT / dataset_dir
    return config, dataset_dir


def verify_dataset(config: dict[str, Any], dataset_dir: Path) -> dict[str, Any]:
    freeze = freeze_dataset(dataset_dir)
    expected = str((config.get("dataset") or {}).get("sha256") or "")
    if freeze.get("dataset_sha256") != expected:
        raise ValueError(
            f"dataset fingerprint differs from config: "
            f"{freeze.get('dataset_sha256')} != {expected}"
        )
    return freeze


def _rotate_clockwise(image: Image.Image, degrees: int) -> Image.Image:
    operations = {
        0: None,
        90: Image.Transpose.ROTATE_270,
        180: Image.Transpose.ROTATE_180,
        270: Image.Transpose.ROTATE_90,
    }
    if degrees not in operations:
        raise ValueError(f"invalid rotation {degrees}")
    operation = operations[degrees]
    return image.transpose(operation) if operation is not None else image


def materialize_normalized_images(
    dataset_dir: Path,
    *,
    dataset_sha256: str,
) -> dict[str, Any]:
    frozen = _read_json(dataset_dir / "frozen_manifest.json")
    output_dir = dataset_dir / "normalized_images"
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[dict[str, Any]] = []
    expected_files: set[str] = set()
    for item in frozen.get("items") or []:
        file_id = str(item["file"])
        expected_files.add(file_id.casefold())
        source = Path(str(item["source_path"]))
        destination = output_dir / file_id
        with Image.open(source) as raw:
            image = ImageOps.exif_transpose(raw)
            image = _rotate_clockwise(
                image, int(item.get("orientation_correction_degrees") or 0)
            )
            if image.mode != "RGB":
                image = image.convert("RGB")
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            image.save(
                temporary,
                format="JPEG",
                quality=100,
                subsampling=0,
                optimize=True,
            )
            temporary.replace(destination)
        items.append(
            {
                "file": file_id,
                "path": str(destination),
                "sha256": _sha256(destination),
                "source_sha256": item["sha256"],
                "orientation_correction_degrees": int(
                    item.get("orientation_correction_degrees") or 0
                ),
            }
        )
    for path in output_dir.iterdir():
        if path.is_file() and path.name.casefold() not in expected_files:
            path.unlink()
    manifest = {
        "schema_version": "selection_normalized_manifest.v1",
        "dataset_sha256": dataset_sha256,
        "orientation_policy": frozen["orientation_policy"],
        "items": items,
    }
    _write_json(dataset_dir / "normalized_manifest.json", manifest)
    return manifest


def prepare_benchmark(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    materialize_images: bool,
) -> dict[str, Any]:
    freeze = verify_dataset(config, dataset_dir)
    labels = _read_jsonl(dataset_dir / "labels.jsonl")
    manifest = _read_json(dataset_dir / "frozen_manifest.json")
    sessions = {
        str(item["file"]): str(item.get("session") or "")
        for item in manifest.get("items") or []
    }
    pack_config = config.get("packs") or {}
    packs = build_fixed_packs(
        labels,
        sessions,
        pack_count=int(pack_config.get("count") or 10),
        seed=int(pack_config.get("seed") or 20260810),
    )
    pack_manifest = {
        "schema_version": "selection_pack_manifest.v1",
        "dataset_sha256": freeze["dataset_sha256"],
        "seed": int(pack_config.get("seed") or 20260810),
        "k": int(pack_config.get("k") or 5),
        "packs": packs,
    }
    _write_json(dataset_dir / "pack_manifest.json", pack_manifest)
    normalized = (
        materialize_normalized_images(
            dataset_dir, dataset_sha256=str(freeze["dataset_sha256"])
        )
        if materialize_images
        else None
    )
    return {
        "dataset_sha256": freeze["dataset_sha256"],
        "pack_count": len(packs),
        "normalized_count": len(normalized["items"]) if normalized else 0,
    }


def materialize_canary(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    output_dir: Path,
    per_category: int,
) -> dict[str, Any]:
    """Copy a deterministic, category-balanced cloud preflight subset."""
    freeze = verify_dataset(config, dataset_dir)
    normalized_path = dataset_dir / "normalized_manifest.json"
    if not normalized_path.is_file():
        raise FileNotFoundError(
            "normalized_manifest.json is missing; run prepare --materialize-images"
        )
    labels = _read_jsonl(dataset_dir / "labels.jsonl")
    normalized = _read_json(normalized_path)
    by_file = {
        str(item["file"]): Path(str(item["path"]))
        for item in normalized.get("items") or []
    }
    selected: list[dict[str, Any]] = []
    for category in ("technical_hard", "semantic_defect", "ordinary", "highlight"):
        rows = sorted(
            (row for row in labels if row.get("sample_type") == category),
            key=lambda row: str(row["file"]),
        )
        if len(rows) < per_category:
            raise ValueError(
                f"not enough {category} rows for per_category={per_category}"
            )
        selected.extend(rows[:per_category])

    output_dir.mkdir(parents=True, exist_ok=True)
    expected = {str(row["file"]) for row in selected}
    existing = {
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png")
    }
    unexpected = sorted(existing - expected)
    if unexpected:
        raise ValueError(
            f"canary output contains unexpected images; use a clean directory: {unexpected}"
        )
    items = []
    for row in selected:
        file_id = str(row["file"])
        source = by_file.get(file_id)
        if source is None or not source.is_file():
            raise FileNotFoundError(f"normalized image is missing for {file_id}")
        destination = output_dir / file_id
        shutil.copy2(source, destination)
        items.append(
            {
                "file": file_id,
                "sample_type": row["sample_type"],
                "sha256": _sha256(destination),
            }
        )
    manifest = {
        "schema_version": "selection_cloud_canary.v1",
        "dataset_sha256": freeze["dataset_sha256"],
        "per_category": per_category,
        "count": len(items),
        "items": items,
    }
    _write_json(output_dir / "canary_manifest.json", manifest)
    return manifest


def materialize_full_runset(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    output_dir: Path,
) -> dict[str, Any]:
    """Copy all normalized frozen images into an isolated full-run directory."""
    freeze = verify_dataset(config, dataset_dir)
    normalized_path = dataset_dir / "normalized_manifest.json"
    if not normalized_path.is_file():
        raise FileNotFoundError(
            "normalized_manifest.json is missing; run prepare --materialize-images"
        )
    normalized = _read_json(normalized_path)
    items = list(normalized.get("items") or [])
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = {str(item["file"]) for item in items}
    existing = {
        path.name
        for path in output_dir.iterdir()
        if path.is_file() and path.suffix.lower() in (".jpg", ".jpeg", ".png")
    }
    unexpected = sorted(existing - expected)
    if unexpected:
        raise ValueError(
            f"full-run output contains unexpected images; use a clean directory: {unexpected}"
        )
    copied = []
    for item in items:
        file_id = str(item["file"])
        source = Path(str(item["path"]))
        if not source.is_file():
            raise FileNotFoundError(f"normalized image is missing for {file_id}")
        destination = output_dir / file_id
        shutil.copy2(source, destination)
        copied.append(
            {
                "file": file_id,
                "sha256": _sha256(destination),
            }
        )
    manifest = {
        "schema_version": "selection_cloud_full_runset.v1",
        "dataset_sha256": freeze["dataset_sha256"],
        "count": len(copied),
        "items": copied,
    }
    _write_json(output_dir / "runset_manifest.json", manifest)
    return manifest


_BAD_STAGE3_OUTCOMES = frozenset(
    {
        "vlm_error",
        "parse_failed",
        "fallback_defaults",
        "degraded_inference",
        "exception",
    }
)


def inspect_cloud_audit(
    *,
    audit_path: Path,
    pipeline_config_path: Path,
    expected_count: int | None,
) -> dict[str, Any]:
    """Validate cloud provenance, schema, fallback, and usage before a full run."""
    pipeline_config = ConfigLoader.load(str(pipeline_config_path))
    model_config = ConfigLoader.get_stage3_model_config(pipeline_config)
    expected_provider = str(model_config.get("provider") or "")
    expected_model = str(model_config.get("model_name") or "")
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(audit_path):
        file_id = str(row.get("file") or row.get("file_name") or row.get("image") or "")
        if file_id:
            latest[file_id] = row

    issues: list[dict[str, str]] = []
    prompt_tokens = 0
    completion_tokens = 0
    usage_rows = 0
    latencies: list[float] = []
    for file_id, row in sorted(latest.items()):
        meta = row.get("stage3_meta") or {}
        outcome = str(meta.get("outcome") or "")
        dimensions = row.get("dimensions")
        gate = row.get("semantic_gate")
        if str(meta.get("provider") or "") != expected_provider:
            issues.append({"file": file_id, "issue": "provider_mismatch"})
        if str(meta.get("model") or "") != expected_model:
            issues.append({"file": file_id, "issue": "model_mismatch"})
        if outcome in _BAD_STAGE3_OUTCOMES or not outcome:
            issues.append({"file": file_id, "issue": f"bad_outcome:{outcome or 'missing'}"})
        if not isinstance(row.get("overall_score", row.get("score")), (int, float)):
            issues.append({"file": file_id, "issue": "missing_score"})
        if not isinstance(dimensions, dict) or len(dimensions) < 8:
            issues.append({"file": file_id, "issue": "incomplete_dimensions"})
        if not isinstance(gate, dict) or str(gate.get("status") or "") not in {
            "pass",
            "reject",
            "review",
            "unknown",
        }:
            issues.append({"file": file_id, "issue": "invalid_semantic_gate"})
        pt = meta.get("prompt_tokens")
        ct = meta.get("completion_tokens")
        if isinstance(pt, (int, float)) and isinstance(ct, (int, float)):
            prompt_tokens += int(pt)
            completion_tokens += int(ct)
            usage_rows += 1
        latency = meta.get("latency_ms")
        if isinstance(latency, (int, float)):
            latencies.append(float(latency))
    if expected_count is not None and len(latest) != expected_count:
        issues.append(
            {
                "file": "*",
                "issue": f"count_mismatch:{len(latest)}!={expected_count}",
            }
        )
    report = {
        "schema_version": "selection_cloud_preflight.v1",
        "passed": not issues,
        "audit": str(audit_path),
        "count": len(latest),
        "expected_count": expected_count,
        "expected_provider": expected_provider,
        "expected_model": expected_model,
        "schema_success_rate": (
            round((len(latest) - len({row["file"] for row in issues if row["file"] != "*"})) / len(latest), 6)
            if latest
            else 0.0
        ),
        "usage_coverage": round(usage_rows / len(latest), 6) if latest else 0.0,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "p50_latency_ms": (
            round(sorted(latencies)[len(latencies) // 2], 3) if latencies else None
        ),
        "fallback_count": sum(
            str((row.get("stage3_meta") or {}).get("outcome") or "")
            in _BAD_STAGE3_OUTCOMES
            for row in latest.values()
        ),
        "issues": issues,
    }
    return report


def _cloud_retry_rows(audit_path: Path) -> list[dict[str, Any]]:
    """Build Stage3 manifest rows for latest retryable cloud audit outcomes."""
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(audit_path):
        file_id = str(row.get("file") or row.get("file_name") or row.get("image") or "")
        if file_id:
            latest[file_id] = row
    retry_rows = []
    for file_id, row in sorted(latest.items()):
        meta = row.get("stage3_meta") or {}
        outcome = str(meta.get("outcome") or "")
        if outcome not in _BAD_STAGE3_OUTCOMES:
            continue
        debug_info = dict(row.get("debug_info") or {})
        tech_score = debug_info.get(
            "effective_tech_score",
            debug_info.get("tech_score", row.get("overall_score", row.get("score", 0))),
        )
        retry_rows.append(
            {
                "file_name": file_id,
                "tech_score": float(tech_score or 0),
                "fast_score": float(row.get("fast_score") or 0),
                "debug_info": debug_info,
            }
        )
    return retry_rows


def retry_cloud_audit(
    *,
    audit_path: Path,
    pipeline_config_path: Path,
    source_dir: Path,
    max_workers: int,
) -> dict[str, Any]:
    """Retry only degraded/error cloud rows using a temporary Stage2 manifest."""
    from services.processor.pipeline_stage_runner import (
        ELIGIBLE_AFTER_S2,
        PipelineStageRunner,
        staged_state_dir,
    )

    retry_rows = _cloud_retry_rows(audit_path)
    if not retry_rows:
        return {"requested": 0, "result": None}
    manifest_path = staged_state_dir(source_dir) / ELIGIBLE_AFTER_S2
    original = manifest_path.read_bytes() if manifest_path.is_file() else None
    try:
        with manifest_path.open("w", encoding="utf-8") as handle:
            for row in retry_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        runner = PipelineStageRunner(
            config_path=str(pipeline_config_path),
            source_dir=str(source_dir),
            trace_id="selection-cloud-retry",
            job_id=None,
            worker_id=0,
        )
        result = runner.run_stage3_vlm(
            max_workers=max(1, max_workers),
            conn=None,
            enable_checkpoint=False,
        )
    finally:
        if original is None:
            manifest_path.unlink(missing_ok=True)
        else:
            manifest_path.write_bytes(original)
    return {
        "requested": len(retry_rows),
        "files": [str(row["file_name"]) for row in retry_rows],
        "result": result,
    }


def run_stage1_baseline(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    pipeline_config_path: Path,
    output_path: Path,
    max_workers: int,
) -> dict[str, Any]:
    """Run the production OpenCV gate and emit scorer-compatible predictions."""
    freeze = verify_dataset(config, dataset_dir)
    normalized_path = dataset_dir / "normalized_manifest.json"
    if not normalized_path.is_file():
        raise FileNotFoundError(
            "normalized_manifest.json is missing; run prepare --materialize-images"
        )
    normalized = _read_json(normalized_path)
    if normalized.get("dataset_sha256") != freeze["dataset_sha256"]:
        raise ValueError("normalized images belong to another dataset")
    pipeline_config = ConfigLoader.load(str(pipeline_config_path))
    started = time.monotonic()

    def assess(item: dict[str, Any]) -> dict[str, Any]:
        file_id = str(item["file"])
        t0 = time.monotonic()
        passed, reason, tech_score, debug_info = assess_stage1_opencv(
            pipeline_config, str(item["path"])
        )
        return {
            "file": file_id,
            "stage1_reject": not passed,
            "stage1_reasons": [] if passed else [reason],
            "semantic_reject": False,
            "stage2_reject": False,
            "stage2_reasons": [],
            "stage2_source": "not_implemented_in_production",
            "overall_score": tech_score,
            "score_source": "technical_only",
            "latency_ms": round((time.monotonic() - t0) * 1000, 3),
            "debug_info": debug_info,
        }

    rows: list[dict[str, Any]] = []
    workers = max(1, max_workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(assess, item): str(item["file"])
            for item in normalized.get("items") or []
        }
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: str(row["file"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    metadata = {
        "schema_version": "selection_predictions.v1",
        "mode": "stage1_only",
        "count": len(rows),
        "stage1_rejected": sum(bool(row["stage1_reject"]) for row in rows),
        "stage2_available": False,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "pipeline_config_sha256": _sha256(pipeline_config_path),
        "output": str(output_path),
    }
    _write_json(output_path.with_suffix(".meta.json"), metadata)
    return metadata


def _selection_policy_flags(
    *,
    stage1_reject: bool,
    semantic_gate_status: str,
    has_semantic_gate: bool,
    semantic_gate: dict[str, Any] | None,
    tags: set[str],
    score: float,
    keep_threshold: float,
    selection_policy: str,
) -> tuple[bool, bool]:
    gate_enabled = selection_policy != "off"
    selection_eligible = not stage1_reject and (
        not gate_enabled or semantic_gate_status == "pass"
    )
    semantic_reject = gate_enabled and (
        not stage1_reject
        and (
            semantic_gate.get("status") == "reject"
            if has_semantic_gate and semantic_gate is not None
            else ("low_quality" in tags or score < keep_threshold)
        )
    )
    return selection_eligible, semantic_reject


def adapt_pipeline_audit(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    audit_path: Path,
    pipeline_config_path: Path,
    output_path: Path,
    mode: str,
    selection_policy: str = "semantic_gate",
) -> dict[str, Any]:
    """Convert production audit rows into the frozen evaluation schema."""
    verify_dataset(config, dataset_dir)
    labels = _read_jsonl(dataset_dir / "labels.jsonl")
    pipeline_config = ConfigLoader.load(str(pipeline_config_path))
    keep_threshold = float(
        ConfigLoader.get_classification_thresholds(pipeline_config)["keep_threshold"]
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(audit_path):
        file_id = str(row.get("file") or row.get("file_name") or row.get("image") or "")
        if file_id:
            latest[file_id] = row
    label_ids = {str(row["file"]) for row in labels}
    if set(latest) != label_ids:
        raise ValueError(
            f"audit ids differ from labels; missing={sorted(label_ids - set(latest))}, "
            f"extra={sorted(set(latest) - label_ids)}"
        )

    predictions = []
    retryable_tags = {"vlm_error", "pipeline_error"}
    for file_id in sorted(label_ids):
        audit = latest[file_id]
        tags = {str(tag) for tag in audit.get("tags") or []}
        if tags & retryable_tags:
            raise ValueError(f"{file_id}: retryable pipeline placeholder in audit")
        stage1_reject = "technical_issue" in tags
        score = float(audit.get("overall_score", audit.get("score", 0)) or 0)
        semantic_gate = audit.get("semantic_gate")
        has_semantic_gate = isinstance(semantic_gate, dict)
        semantic_gate_status = (
            str(semantic_gate.get("status") or "").strip().lower()
            if has_semantic_gate
            else "missing"
        )
        selection_eligible, semantic_reject = _selection_policy_flags(
            stage1_reject=stage1_reject,
            semantic_gate_status=semantic_gate_status,
            has_semantic_gate=has_semantic_gate,
            semantic_gate=semantic_gate,
            tags=tags,
            score=score,
            keep_threshold=keep_threshold,
            selection_policy=selection_policy,
        )
        stage3_meta = audit.get("stage3_meta") or {}
        dimensions = audit.get("dimensions") or {}
        outcome = str(stage3_meta.get("outcome") or "")
        cloud_attempted = (
            bool(stage3_meta.get("provider"))
            and bool(stage3_meta.get("model"))
            and outcome not in {"skipped_stage3_gating", "skipped_near_duplicate"}
        )
        predictions.append(
            {
                "file": file_id,
                "stage1_reject": stage1_reject,
                "stage1_reasons": (
                    [str(audit.get("weakness") or audit.get("reason") or "")]
                    if stage1_reject
                    else []
                ),
                "semantic_reject": semantic_reject,
                "semantic_reasons": (
                    [str(audit.get("weakness") or audit.get("reason") or "")]
                    if semantic_reject
                    else []
                ),
                "semantic_gate": semantic_gate,
                "semantic_gate_status": semantic_gate_status,
                "selection_eligible": selection_eligible,
                "semantic_source": (
                    "stage3_semantic_gate"
                    if has_semantic_gate
                    else "legacy_pipeline_trash_threshold"
                ),
                # Compatibility for older scorer/report readers.
                "stage2_reject": semantic_reject,
                "overall_score": score,
                "score_source": "technical_only" if stage1_reject else "vlm",
                "dimensions": audit.get("dimensions") or {},
                "tags": sorted(tags),
                "latency_ms": stage3_meta.get("latency_ms"),
                "provider": stage3_meta.get("provider"),
                "model": stage3_meta.get("model"),
                "stage3_outcome": outcome,
                "cloud_attempted": cloud_attempted,
                "fallback_used": outcome in _BAD_STAGE3_OUTCOMES,
                "prompt_tokens": stage3_meta.get("prompt_tokens"),
                "completion_tokens": stage3_meta.get("completion_tokens"),
                "schema_valid": cloud_attempted and (
                    isinstance(audit.get("semantic_gate"), dict)
                    and isinstance(audit.get("overall_score", audit.get("score")), (int, float))
                ),
                "full_dimensions_valid": (
                    cloud_attempted
                    and isinstance(dimensions, dict)
                    and len(dimensions) >= 8
                ),
            }
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    metadata = {
        "schema_version": "selection_predictions.v1",
        "mode": mode,
        "selection_policy": selection_policy,
        "count": len(predictions),
        "stage1_rejected": sum(row["stage1_reject"] for row in predictions),
        "semantic_rejected": sum(row["semantic_reject"] for row in predictions),
        "fallback_count": sum(row["fallback_used"] for row in predictions),
        "schema_valid_count": sum(row["schema_valid"] for row in predictions),
        "usage_count": sum(
            isinstance(row.get("prompt_tokens"), (int, float))
            and isinstance(row.get("completion_tokens"), (int, float))
            for row in predictions
        ),
        "semantic_gate_note": (
            "Semantic observations are retained for diagnostics, but automatic ranking "
            "ignores them because selection_policy=off."
            if selection_policy == "off"
            else "semantic_reject uses Stage3 semantic_gate when present; legacy audit "
            "rows fall back to the historical trash threshold. Automatic ranking is "
            "stricter: only semantic_gate.status=pass is selection_eligible."
        ),
        "pipeline_config_sha256": _sha256(pipeline_config_path),
        "audit_sha256": _sha256(audit_path),
        "output": str(output_path),
    }
    _write_json(output_path.with_suffix(".meta.json"), metadata)
    return metadata


def _ranking(
    labels: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    pack_manifest: dict[str, Any],
    defects: set[str],
    acceptable: set[str],
    *,
    global_k: int,
    pack_k: int,
) -> dict[str, Any]:
    predictions_by_file = {str(row["file"]): row for row in predictions}

    def ranked(files: list[str]) -> list[str]:
        survivors = [
            file_id
            for file_id in files
            if bool(predictions_by_file[file_id].get("selection_eligible"))
        ]
        return sorted(
            survivors,
            key=lambda file_id: (
                -float(predictions_by_file[file_id].get("overall_score") or 0),
                file_id,
            ),
        )

    all_files = [str(row["file"]) for row in labels]
    global_result = score_ranked_selection(
        ranked(all_files),
        defects=defects,
        acceptable=acceptable,
        k=global_k,
    )
    pack_results = []
    for pack in pack_manifest.get("packs") or []:
        files = [str(file_id) for file_id in pack["files"]]
        result = score_ranked_selection(
            ranked(files),
            defects=defects,
            acceptable=acceptable.intersection(files),
            k=pack_k,
        )
        pack_results.append({"id": pack["id"], **result})
    pack_selected_total = sum(row["selected_count"] for row in pack_results)
    pack_defect_count = sum(row["defect_count"] for row in pack_results)
    return {
        "global": global_result,
        "packs": pack_results,
        "macro_pack_overlap_at_k": round(
            sum(row["overlap_at_k"] for row in pack_results) / len(pack_results),
            6,
        ),
        "worst_pack_overlap_at_k": min(
            row["overlap_at_k"] for row in pack_results
        ),
        "pack_zero_blunder_passed": all(
            row["zero_blunder_passed"] for row in pack_results
        ),
        "pack_selected_total": pack_selected_total,
        "pack_defect_count": pack_defect_count,
        "pack_defect_rate": round(
            pack_defect_count / pack_selected_total, 6
        ) if pack_selected_total else None,
        "zero_observation_upper_95": (
            round(3 / pack_selected_total, 6)
            if pack_selected_total and pack_defect_count == 0
            else None
        ),
    }


def _score_quality(
    labels: list[dict[str, Any]], predictions: list[dict[str, Any]]
) -> dict[str, Any]:
    labels_by_file = {str(row["file"]): row for row in labels}
    human: list[float] = []
    model: list[float] = []
    for row in predictions:
        if row.get("score_source") not in (None, "vlm"):
            continue
        score = row.get("overall_score")
        truth = labels_by_file[str(row["file"])].get("overall")
        if isinstance(score, (int, float)) and isinstance(truth, (int, float)):
            human.append(float(truth))
            model.append(float(score))
    dimensions: dict[str, Any] = {}
    dimension_keys = list((labels[0].get("dims") or {}).keys()) if labels else []
    for key in dimension_keys:
        human_dimension: list[float] = []
        model_dimension: list[float] = []
        for row in predictions:
            if row.get("score_source") not in (None, "vlm"):
                continue
            predicted = (row.get("dimensions") or {}).get(key)
            truth = (labels_by_file[str(row["file"])].get("dims") or {}).get(key)
            if isinstance(predicted, (int, float)) and isinstance(truth, (int, float)):
                human_dimension.append(float(truth))
                model_dimension.append(float(predicted))
        dimensions[key] = {
            "n": len(human_dimension),
            "mae": (
                round(mae(human_dimension, model_dimension), 6)
                if human_dimension
                else None
            ),
            "spearman": (
                round(spearman(human_dimension, model_dimension), 6)
                if len(human_dimension) >= 2
                else None
            ),
        }
    dimension_maes = [
        row["mae"] for row in dimensions.values() if row["mae"] is not None
    ]
    return {
        "n": len(human),
        "coverage": round(len(human) / len(labels), 6) if labels else None,
        "spearman": round(spearman(human, model), 6) if len(human) >= 2 else None,
        "mae": round(mae(human, model), 6) if human else None,
        "rmse": round(rmse(human, model), 6) if human else None,
        "bias": bias_stats(human, model),
        "dimensions": dimensions,
        "dimension_macro_mae": (
            round(sum(dimension_maes) / len(dimension_maes), 6)
            if dimension_maes
            else None
        ),
    }


def _validity_metrics(predictions: list[dict[str, Any]]) -> dict[str, Any]:
    from quality.validity import parse_meta_from_record, summarize_validity

    return summarize_validity(parse_meta_from_record(row) for row in predictions)


def _runtime_metrics(
    predictions: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, Any]:
    latencies = sorted(
        float(row["latency_ms"])
        for row in predictions
        if isinstance(row.get("latency_ms"), (int, float))
    )

    def percentile(q: float) -> float | None:
        if not latencies:
            return None
        index = min(len(latencies) - 1, int(round(q * (len(latencies) - 1))))
        return round(latencies[index], 3)

    prompt_tokens = sum(
        int(row["prompt_tokens"])
        for row in predictions
        if isinstance(row.get("prompt_tokens"), (int, float))
    )
    completion_tokens = sum(
        int(row["completion_tokens"])
        for row in predictions
        if isinstance(row.get("completion_tokens"), (int, float))
    )
    cloud_predictions = [row for row in predictions if bool(row.get("cloud_attempted"))]
    usage_count = sum(
        isinstance(row.get("prompt_tokens"), (int, float))
        and isinstance(row.get("completion_tokens"), (int, float))
        for row in cloud_predictions
    )
    cost_config = config.get("cost") or {}
    input_rate = cost_config.get("input_usd_per_million_tokens")
    output_rate = cost_config.get("output_usd_per_million_tokens")
    estimated_cost = (
        round(
            prompt_tokens * float(input_rate) / 1_000_000
            + completion_tokens * float(output_rate) / 1_000_000,
            6,
        )
        if isinstance(input_rate, (int, float))
        and isinstance(output_rate, (int, float))
        else None
    )
    return {
        "latency_count": len(latencies),
        "p50_latency_ms": percentile(0.50),
        "p95_latency_ms": percentile(0.95),
        "p99_latency_ms": percentile(0.99),
        "vlm_calls": len(cloud_predictions),
        "fallback_count": sum(bool(row.get("fallback_used")) for row in predictions),
        "schema_success_rate": round(
            sum(bool(row.get("schema_valid")) for row in cloud_predictions)
            / len(cloud_predictions),
            6,
        ) if cloud_predictions else 0.0,
        "full_dimensions_rate": round(
            sum(bool(row.get("full_dimensions_valid")) for row in cloud_predictions)
            / len(cloud_predictions),
            6,
        ) if cloud_predictions else 0.0,
        "usage_coverage": (
            round(usage_count / len(cloud_predictions), 6)
            if cloud_predictions
            else 0.0
        ),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "estimated_cost_usd": estimated_cost,
        "cost_basis": (
            "configured_token_rates"
            if estimated_cost is not None
            else "unavailable_set_cost_rates_in_config"
        ),
    }


def _threshold_failures(
    report: dict[str, Any], config: dict[str, Any], *, mode: str
) -> list[dict[str, Any]]:
    thresholds = config.get("thresholds") or {}
    selection = config.get("selection") or {}
    metrics = report["pipeline_metrics"]
    ranking = report["selection_metrics"]
    gate_off = str(
        (report.get("prediction_metadata") or {}).get("selection_policy") or ""
    ).lower() == "off"
    checks: list[tuple[str, float, str, float]] = []
    if not mode.startswith("cloud_intrinsic"):
        checks.extend(
            [
                (
                    "stage1.technical_recall",
                    metrics["stage1"]["recall"]["value"],
                    ">=",
                    float(thresholds["min_technical_recall"]),
                ),
                (
                    "stage1.ordinary_fpr",
                    metrics["stage1"]["class_rejection"]["ordinary"]["rate"]["value"],
                    "<=",
                    float(thresholds["max_ordinary_fpr"]),
                ),
                (
                    "stage1.highlight_fpr",
                    metrics["stage1"]["class_rejection"]["highlight"]["rate"]["value"],
                    "<=",
                    float(thresholds["max_highlight_fpr"]),
                ),
            ]
        )
    failures = []
    if mode != "stage1_only" and not gate_off and bool(thresholds.get("enforce_semantic_gate")):
        checks.extend(
            [
                (
                    "semantic_gate.recall",
                    metrics["semantic_gate"]["recall"]["value"],
                    ">=",
                    float(thresholds["min_semantic_recall"]),
                ),
                (
                    "semantic_gate.ordinary_fpr",
                    metrics["semantic_gate"]["class_rejection"]["ordinary"][
                        "rate"
                    ]["value"],
                    "<=",
                    float(thresholds["max_ordinary_fpr"]),
                ),
                (
                    "semantic_gate.highlight_fpr",
                    metrics["semantic_gate"]["class_rejection"]["highlight"][
                        "rate"
                    ]["value"],
                    "<=",
                    float(thresholds["max_highlight_fpr"]),
                ),
            ]
        )
    if mode != "stage1_only":
        checks.extend(
            [
                (
                    "selection.global_overlap_at_k",
                    ranking["global"]["overlap_at_k"],
                    ">=",
                    float(selection["min_global_overlap_at_k"]),
                ),
                (
                    "selection.macro_pack_overlap_at_k",
                    ranking["macro_pack_overlap_at_k"],
                    ">=",
                    float(selection["min_pack_macro_overlap_at_k"]),
                ),
                (
                    "selection.global_defects",
                    float(ranking["global"]["defect_count"]),
                    "<=",
                    float(selection["max_selected_defects"]),
                ),
            ]
        )
    if mode != "stage1_only" and not ranking["pack_zero_blunder_passed"]:
        failures.append(
            {
                "metric": "selection.pack_zero_blunder",
                "current": False,
                "operator": "==",
                "threshold": True,
            }
        )
    validity_cfg = config.get("validity") or {}
    validity = report.get("validity") or {}
    if int(validity.get("n_known") or 0) > 0:
        if "max_parse_fail_rate" in validity_cfg:
            checks.append(
                (
                    "validity.parse_fail_rate",
                    float(validity.get("parse_fail_rate") or 0),
                    "<=",
                    float(validity_cfg["max_parse_fail_rate"]),
                )
            )
        if "max_regex_recovery_rate" in validity_cfg:
            checks.append(
                (
                    "validity.regex_recovery_rate",
                    float(validity.get("regex_recovery_rate") or 0),
                    "<=",
                    float(validity_cfg["max_regex_recovery_rate"]),
                )
            )
        if "max_missing_dim_rate" in validity_cfg:
            checks.append(
                (
                    "validity.missing_dim_rate",
                    float(validity.get("missing_dim_rate") or 0),
                    "<=",
                    float(validity_cfg["max_missing_dim_rate"]),
                )
            )
    operational = config.get("operational") or {}
    runtime = report.get("runtime_metrics") or {}
    if mode != "stage1_only":
        if "max_fallback_count" in operational:
            checks.append(
                (
                    "operational.fallback_count",
                    float(runtime.get("fallback_count") or 0),
                    "<=",
                    float(operational["max_fallback_count"]),
                )
            )
        if "min_schema_success_rate" in operational:
            checks.append(
                (
                    "operational.schema_success_rate",
                    float(runtime.get("schema_success_rate") or 0),
                    ">=",
                    float(operational["min_schema_success_rate"]),
                )
            )
    for metric, value, operator, target in checks:
        failed = value < target if operator == ">=" else value > target
        if failed:
            failures.append(
                {
                    "metric": metric,
                    "current": value,
                    "operator": operator,
                    "threshold": target,
                }
            )
    return failures


def _render_markdown(report: dict[str, Any]) -> str:
    pipeline = report["pipeline_metrics"]
    ranking = report["selection_metrics"]
    failures = report["failures"]

    def pct(value: float) -> str:
        return f"{value * 100:.1f}%"

    lines = [
        "# Selection v1 Quality Evaluation",
        "",
        f"- Run ID: `{report['run_meta']['run_id']}`",
        f"- Dataset: `{report['run_meta']['dataset_sha256']}`",
        f"- Git commit: `{report['run_meta']['git_commit']}`",
        f"- Result: **{report.get('status', 'PASS' if report['passed'] else 'FAIL')}**",
        "",
        "## Core metrics",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Stage 1 technical recall | {pct(pipeline['stage1']['recall']['value'])} |",
        f"| Final semantic gate recall | {pct(pipeline['semantic_gate']['recall']['value'])} |",
        f"| Pipeline defect recall | {pct(pipeline['pipeline']['recall']['value'])} |",
        f"| Global defects selected | {ranking['global']['defect_count']} |",
        f"| Global Overlap@K | {pct(ranking['global']['overlap_at_k'])} |",
        f"| Macro Pack Overlap@K | {pct(ranking['macro_pack_overlap_at_k'])} |",
        "",
        "## Gate failures",
        "",
    ]
    if failures:
        lines.extend(
            f"- `{row['metric']}`: {row['current']} {row['operator']} {row['threshold']}"
            for row in failures
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Error cases",
            "",
            f"- Pipeline FP/FN: {len(pipeline['errors'])}",
            f"- Failed packs: {sum(not row['zero_blunder_passed'] for row in ranking['packs'])}",
            "",
        ]
    )
    return "\n".join(lines)


def score_predictions(
    config: dict[str, Any],
    dataset_dir: Path,
    *,
    predictions_path: Path,
    output_dir: Path,
    model: str | None,
    config_path: Path,
) -> dict[str, Any]:
    freeze = verify_dataset(config, dataset_dir)
    labels = _read_jsonl(dataset_dir / "labels.jsonl")
    predictions = _read_jsonl(predictions_path)
    predictions_meta_path = predictions_path.with_suffix(".meta.json")
    predictions_meta = (
        _read_json(predictions_meta_path)
        if predictions_meta_path.is_file()
        else {"mode": "full"}
    )
    mode = str(predictions_meta.get("mode") or "full")
    pack_manifest = _read_json(dataset_dir / "pack_manifest.json")
    if pack_manifest.get("dataset_sha256") != freeze["dataset_sha256"]:
        raise ValueError("pack manifest belongs to another dataset")
    defects = set(_read_json(dataset_dir / "defects.json"))
    acceptable = set(_read_json(dataset_dir / "acceptable_pool.json"))
    pipeline_metrics = evaluate_pipeline(labels, predictions)
    selection_cfg = config.get("selection") or {}
    pack_cfg = config.get("packs") or {}
    selection_metrics = _ranking(
        labels,
        predictions,
        pack_manifest,
        defects,
        acceptable,
        global_k=int(selection_cfg.get("global_k") or 10),
        pack_k=int(pack_cfg.get("k") or 5),
    )
    now = datetime.now(timezone.utc)
    report = {
        "schema_version": "selection_quality_report.v1",
        "run_meta": {
            "run_id": now.strftime("%Y%m%dT%H%M%SZ"),
            "generated_at": now.isoformat(),
            "git_commit": _git_commit(),
            "dataset_sha256": freeze["dataset_sha256"],
            "config_sha256": _sha256(config_path),
            "predictions_sha256": _sha256(predictions_path),
            "model": model,
            "mode": mode,
        },
        "pipeline_metrics": pipeline_metrics,
        "selection_metrics": selection_metrics,
        "score_quality": (
            {"available": False, "reason": "Stage1 tech_score is not an overall score"}
            if mode == "stage1_only"
            else {"available": True, **_score_quality(labels, predictions)}
        ),
        "runtime_metrics": _runtime_metrics(predictions, config),
        "validity": _validity_metrics(predictions),
        "prediction_metadata": predictions_meta,
        "resolved_thresholds": {
            "thresholds": config.get("thresholds") or {},
            "selection": config.get("selection") or {},
            "regression": config.get("regression") or {},
        },
    }
    report["failures"] = _threshold_failures(report, config, mode=mode)
    report["passed"] = not report["failures"]
    report["cloud_failures"] = (
        report["failures"]
        if mode == "stage1_only"
        else [
            row
            for row in report["failures"]
            if not str(row.get("metric") or "").startswith("stage1.")
        ]
    )
    report["status"] = (
        "PASS"
        if report["passed"]
        else "CONDITIONAL_PASS"
        if mode != "stage1_only" and not report["cloud_failures"]
        else "FAIL"
    )
    report["release_eligible"] = mode != "stage1_only" and report["passed"]
    report["gate"] = {
        "passed": report["passed"],
        "release_eligible": report["release_eligible"],
        "failures": report["failures"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "report.json", report)
    with (output_dir / "error_cases.jsonl").open("w", encoding="utf-8") as handle:
        for row in pipeline_metrics["errors"]:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (output_dir / "report.md").write_text(
        _render_markdown(report) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs/eval/selection_v1.yaml",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--materialize-images", action="store_true")
    canary = subparsers.add_parser("materialize-canary")
    canary.add_argument("--output", type=Path, required=True)
    canary.add_argument("--per-category", type=int, default=3)
    full = subparsers.add_parser("materialize-full")
    full.add_argument("--output", type=Path, required=True)
    inspect = subparsers.add_parser("inspect-cloud-audit")
    inspect.add_argument("--audit", type=Path, required=True)
    inspect.add_argument(
        "--pipeline-config",
        type=Path,
        default=ROOT / "configs/eval_stage3.yaml",
    )
    inspect.add_argument("--expected-count", type=int)
    retry = subparsers.add_parser("retry-cloud-audit")
    retry.add_argument("--audit", type=Path, required=True)
    retry.add_argument("--source-dir", type=Path, required=True)
    retry.add_argument(
        "--pipeline-config",
        type=Path,
        default=ROOT / "configs/eval_stage3.yaml",
    )
    retry.add_argument("--max-workers", type=int, default=2)
    stage1 = subparsers.add_parser("run-stage1")
    stage1.add_argument(
        "--pipeline-config",
        type=Path,
        default=ROOT / "configs/livehouse.yaml",
    )
    stage1.add_argument("--output", type=Path, required=True)
    stage1.add_argument("--max-workers", type=int, default=4)
    adapt = subparsers.add_parser("adapt-pipeline")
    adapt.add_argument("--audit", type=Path, required=True)
    adapt.add_argument(
        "--pipeline-config",
        type=Path,
        default=ROOT / "configs/livehouse.yaml",
    )
    adapt.add_argument("--output", type=Path, required=True)
    adapt.add_argument("--mode", default="full_pipeline")
    adapt.add_argument(
        "--selection-policy",
        choices=("semantic_gate", "off"),
        default="semantic_gate",
        help="whether semantic observations can reject or block ranking candidates",
    )
    score = subparsers.add_parser("score")
    score.add_argument("--predictions", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--model")
    args = parser.parse_args()
    config, dataset_dir = load_config(args.config)
    if args.command == "prepare":
        result = prepare_benchmark(
            config,
            dataset_dir,
            materialize_images=args.materialize_images,
        )
    elif args.command == "materialize-canary":
        result = materialize_canary(
            config,
            dataset_dir,
            output_dir=args.output,
            per_category=max(1, args.per_category),
        )
    elif args.command == "materialize-full":
        result = materialize_full_runset(
            config,
            dataset_dir,
            output_dir=args.output,
        )
    elif args.command == "inspect-cloud-audit":
        result = inspect_cloud_audit(
            audit_path=args.audit,
            pipeline_config_path=args.pipeline_config,
            expected_count=args.expected_count,
        )
    elif args.command == "retry-cloud-audit":
        result = retry_cloud_audit(
            audit_path=args.audit,
            pipeline_config_path=args.pipeline_config,
            source_dir=args.source_dir,
            max_workers=args.max_workers,
        )
    elif args.command == "run-stage1":
        result = run_stage1_baseline(
            config,
            dataset_dir,
            pipeline_config_path=args.pipeline_config,
            output_path=args.output,
            max_workers=args.max_workers,
        )
    elif args.command == "adapt-pipeline":
        result = adapt_pipeline_audit(
            config,
            dataset_dir,
            audit_path=args.audit,
            pipeline_config_path=args.pipeline_config,
            output_path=args.output,
            mode=args.mode,
            selection_policy=args.selection_policy,
        )
    else:
        result = score_predictions(
            config,
            dataset_dir,
            predictions_path=args.predictions,
            output_dir=args.output,
            model=args.model,
            config_path=args.config,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "inspect-cloud-audit" and not result.get("passed"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
