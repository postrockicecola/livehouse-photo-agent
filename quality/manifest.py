"""Build ``version_manifest.v1`` from eval config + Stage3 prompt registry.

Usage::

    python -m quality.manifest \\
        --config configs/eval_stage3.yaml \\
        --labels data/eval/labels.jsonl \\
        --out quality/store/manifests/latest.json

Or from code::

    from quality.manifest import build_version_manifest, write_version_manifest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

_REPO = Path(__file__).resolve().parents[1]
_EVAL_PROTOCOL = "quality_protocol_v1"
_WORKFLOW_ID = "pipeline_stage3"
_WORKFLOW_VERSION = "0.1.0"
_DEFAULT_DATASET_NAME = "golden_core"
_DEFAULT_DATASET_VERSION = "0.1.0"


def canonical_json_bytes(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON for hashing (sorted keys, compact)."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path | str | None) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_sha_full() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty() -> bool:
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=str(_REPO),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return bool(out.strip())
    except (OSError, subprocess.CalledProcessError):
        return False


def _load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"config root must be a mapping: {path}")
    return raw


def prompt_registry_bundle() -> dict[str, Any]:
    """Canonical prompt bytes source (registry + rubric keys)."""
    from services.processor.stages.stage3_prompt_registry import (
        PROMPT_BLOCKS,
        PROMPT_VERSION,
        STAGE3_COMPACT_EXEMPLAR,
    )
    from utils.stage3_dimensions import STAGE3_DIM_KEYS, STAGE3_DIM_PROMPT_LINES

    return {
        "prompt_id": "stage3",
        "version": PROMPT_VERSION,
        "blocks": dict(PROMPT_BLOCKS),
        "exemplar": STAGE3_COMPACT_EXEMPLAR,
        "dim_keys": list(STAGE3_DIM_KEYS),
        "dim_prompt_lines": dict(STAGE3_DIM_PROMPT_LINES),
    }


def prompt_content_hash() -> tuple[str, str, str]:
    """Return ``(prompt_id, version, content_hash)``."""
    bundle = prompt_registry_bundle()
    return bundle["prompt_id"], bundle["version"], sha256_hex(canonical_json_bytes(bundle))


def eval_relevant_config_subset(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize workflow-relevant knobs (excludes secrets / endpoints)."""
    processing = raw.get("processing") if isinstance(raw.get("processing"), dict) else {}
    subset: dict[str, Any] = {
        "quality_thresholds": raw.get("quality_thresholds"),
        "fast_aesthetic": raw.get("fast_aesthetic"),
        "evaluation": raw.get("evaluation"),
        "classification": raw.get("classification"),
        "stage3": raw.get("stage3"),
        "processing": {
            "stage2_prefilter": processing.get("stage2_prefilter"),
            "stage3_vlm_cache": processing.get("stage3_vlm_cache"),
            "stage3_gating": processing.get("stage3_gating"),
            "gallery_view_dedupe": processing.get("gallery_view_dedupe"),
        },
        "stage4_editing": {
            "enabled": (raw.get("stage4_editing") or {}).get("enabled")
            if isinstance(raw.get("stage4_editing"), dict)
            else None
        },
    }
    return subset


def workflow_config_hash(raw: Mapping[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(eval_relevant_config_subset(raw)))


def _endpoint_class(provider: str | None, endpoint: str | None) -> str:
    prov = (provider or "unknown").strip().lower()
    ep = (endpoint or "").strip().lower()
    if prov == "mock":
        return "mock"
    if "localhost" in ep or "127.0.0.1" in ep:
        return f"local_{prov}"
    if ep:
        return f"remote_{prov}"
    return prov


def _stage3_strategy(raw: Mapping[str, Any]) -> str | None:
    stage3 = raw.get("stage3") if isinstance(raw.get("stage3"), dict) else {}
    strat = stage3.get("strategy")
    return str(strat) if strat else None


def _gating_pin(raw: Mapping[str, Any]) -> bool | str | None:
    processing = raw.get("processing") if isinstance(raw.get("processing"), dict) else {}
    if "stage3_gating" not in processing:
        return False
    gating = processing.get("stage3_gating")
    if isinstance(gating, dict):
        if "enabled" in gating:
            return bool(gating.get("enabled"))
        return "stage3_gating"
    if isinstance(gating, bool):
        return gating
    return None


def attach_manifest_hash(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compute and set ``version_manifest_hash`` (hash excludes the field itself)."""
    body = {k: v for k, v in manifest.items() if k != "version_manifest_hash"}
    manifest["version_manifest_hash"] = sha256_hex(canonical_json_bytes(body))
    return manifest


def build_version_manifest(
    *,
    config_path: str | Path = "configs/eval_stage3.yaml",
    labels_path: str | Path | None = None,
    dataset_manifest_path: str | Path | None = None,
    dataset_name: str = _DEFAULT_DATASET_NAME,
    dataset_version: str = _DEFAULT_DATASET_VERSION,
    manifest_id: str | None = None,
    workflow_id: str = _WORKFLOW_ID,
    workflow_version: str = _WORKFLOW_VERSION,
    eval_protocol: str = _EVAL_PROTOCOL,
    notes: str | None = None,
    tags: list[str] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Assemble a frozen ``version_manifest.v1`` document."""
    cfg_path = Path(config_path)
    if not cfg_path.is_file():
        # Allow repo-relative resolution from CWD or repo root.
        alt = _REPO / config_path
        if alt.is_file():
            cfg_path = alt
        else:
            raise FileNotFoundError(f"config not found: {config_path}")

    raw = _load_yaml(cfg_path)
    model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
    prompt_id, prompt_version, prompt_hash = prompt_content_hash()

    labels_sha = file_sha256(labels_path)
    ds_manifest = dataset_manifest_path
    if ds_manifest is None and labels_path:
        candidate = Path(labels_path).resolve().parent / "manifest.json"
        if candidate.is_file():
            ds_manifest = candidate
    manifest_sha = file_sha256(ds_manifest)

    provider = model.get("provider") or "ollama"
    model_name = model.get("model_name") or "unknown"
    temperature = model.get("temperature")
    if temperature is None:
        temperature = 0.0

    mid = manifest_id or (
        f"{workflow_id}_{prompt_version}_{str(model_name).replace(':', '_')}"
    )

    fb = model.get("fallback_model_name") or None
    if isinstance(fb, str) and not fb.strip():
        fb = None

    doc: dict[str, Any] = {
        "schema_version": "version_manifest.v1",
        "manifest_id": mid,
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        "prompt": {
            "prompt_id": prompt_id,
            "version": prompt_version,
            "content_hash": prompt_hash,
            "profile": prompt_version,
        },
        "model": {
            "provider": str(provider),
            "model_name": str(model_name),
            "temperature": float(temperature),
            "num_predict": model.get("num_predict"),
            "fallback_model_name": fb,
            "endpoint_class": _endpoint_class(
                str(provider) if provider else None,
                str(model.get("endpoint") or "") or None,
            ),
            "agent_chat_model": model.get("agent_chat_model") or None,
            "digest": None,
        },
        "workflow": {
            "workflow_id": workflow_id,
            "version": workflow_version,
            "config_hash": workflow_config_hash(raw),
            "stage3_strategy": _stage3_strategy(raw),
            "gating": _gating_pin(raw),
            "planner": None,
        },
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
        },
        "code": {
            "git_sha": _git_sha_full(),
            "dirty": _git_dirty(),
            "ref": "HEAD",
        },
        "eval_protocol": eval_protocol,
    }
    if labels_sha:
        doc["dataset"]["labels_sha256"] = labels_sha
    if manifest_sha:
        doc["dataset"]["manifest_sha256"] = manifest_sha
    if notes:
        doc["notes"] = notes
    if tags:
        doc["tags"] = list(tags)

    # Record config path in notes-friendly tags for operators (optional).
    doc.setdefault("tags", [])
    cfg_tag = f"config:{cfg_path.as_posix()}"
    if cfg_tag not in doc["tags"]:
        doc["tags"].append(cfg_tag)

    return attach_manifest_hash(doc)


def compact_manifest_ref(manifest: Mapping[str, Any]) -> dict[str, str]:
    return {
        "manifest_id": str(manifest.get("manifest_id") or ""),
        "version_manifest_hash": str(manifest.get("version_manifest_hash") or ""),
    }


def write_version_manifest(path: str | Path, manifest: Mapping[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(manifest)
    if "version_manifest_hash" not in payload:
        attach_manifest_hash(payload)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def stamp_report_with_manifest(
    report: dict[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach compact manifest ref on the report (and under protocol.extra)."""
    ref = compact_manifest_ref(manifest)
    report["version_manifest"] = ref
    proto = report.get("protocol")
    if isinstance(proto, dict):
        extra = dict(proto.get("extra") or {})
        extra["version_manifest"] = ref
        extra["prompt_version"] = (manifest.get("prompt") or {}).get("version")
        extra["prompt_content_hash"] = (manifest.get("prompt") or {}).get("content_hash")
        extra["workflow_config_hash"] = (manifest.get("workflow") or {}).get("config_hash")
        proto["extra"] = extra
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build version_manifest.v1 from eval config + prompt registry"
    )
    parser.add_argument(
        "--config",
        default="configs/eval_stage3.yaml",
        help="eval YAML config (default: %(default)s)",
    )
    parser.add_argument("--labels", default=None, help="labels JSONL for dataset hash")
    parser.add_argument(
        "--dataset-manifest",
        default=None,
        dest="dataset_manifest",
        help="dataset manifest.json (default: sibling of --labels)",
    )
    parser.add_argument("--dataset-name", default=_DEFAULT_DATASET_NAME)
    parser.add_argument("--dataset-version", default=_DEFAULT_DATASET_VERSION)
    parser.add_argument("--manifest-id", default=None, dest="manifest_id")
    parser.add_argument(
        "--out",
        default="quality/store/manifests/latest.json",
        help="output path (default: %(default)s)",
    )
    parser.add_argument("--notes", default=None)
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="repeatable tag",
    )
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="print version_manifest_hash to stdout only",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_version_manifest(
            config_path=args.config,
            labels_path=args.labels,
            dataset_manifest_path=args.dataset_manifest,
            dataset_name=args.dataset_name,
            dataset_version=args.dataset_version,
            manifest_id=args.manifest_id,
            notes=args.notes,
            tags=list(args.tag) or None,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.print_hash:
        print(manifest["version_manifest_hash"])
        return 0

    out = write_version_manifest(args.out, manifest)
    # Validate against Phase-0 checker when available.
    try:
        from quality.validate_contracts import validate_document

        errors = validate_document(manifest, str(out))
        if errors:
            print(f"wrote {out} but validation failed:", file=sys.stderr)
            for e in errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
    except Exception:
        pass

    print(f"wrote {out}")
    print(f"manifest_id={manifest['manifest_id']}")
    print(f"version_manifest_hash={manifest['version_manifest_hash']}")
    print(f"prompt.version={manifest['prompt']['version']}")
    print(f"prompt.content_hash={manifest['prompt']['content_hash'][:16]}…")
    print(f"workflow.config_hash={manifest['workflow']['config_hash'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
