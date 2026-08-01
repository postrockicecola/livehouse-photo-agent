#!/usr/bin/env python3
"""Phase-0 contract checks for quality/schemas (stdlib only).

Usage:
  python quality/validate_contracts.py
  python quality/validate_contracts.py path/to/doc.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent
_EXAMPLES = _ROOT / "schemas" / "examples"

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DIM_KEYS = {
    "focus_sharpness",
    "exposure_control",
    "noise_cleanliness",
    "composition_framing",
    "light_color_character",
    "moment_peak",
    "atmosphere_impact",
    "deliverable_subject",
}
_SPLITS = {
    "smoke",
    "core",
    "hard",
    "regression",
    "agent_chat",
}
_SUITES = {
    "stage3_scoring",
    "stage3_selection",
    "gating_policy",
    "agent_chat_cases",
    "e2e_gallery_contract",
}

_AGENT_CASE_SPLITS = {"smoke", "core", "regression", "hard"}
_AGENT_CASE_TAGS = {
    "routed",
    "semantic",
    "memory",
    "vibe",
    "export",
    "control",
    "negation",
    "compound",
    "search",
    "hard",
    "smoke",
    "regression",
}


def _err(path: str, msg: str) -> str:
    return f"{path}: {msg}"


def _require(obj: dict[str, Any], keys: list[str], path: str) -> list[str]:
    return [_err(path, f"missing required field '{k}'") for k in keys if k not in obj]


def validate_label(label: Any, path: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(label, dict):
        return [_err(path, "label must be object")]
    overall = label.get("overall")
    if overall is not None and not (isinstance(overall, (int, float)) and 0 <= float(overall) <= 100):
        errors.append(_err(path, "overall must be null or in [0, 100]"))
    dims = label.get("dims")
    if dims is not None:
        if not isinstance(dims, dict):
            errors.append(_err(path, "dims must be object"))
        else:
            unknown = set(dims) - _DIM_KEYS
            if unknown:
                errors.append(_err(path, f"unknown dim keys: {sorted(unknown)}"))
            for k, v in dims.items():
                if v is not None and not (isinstance(v, (int, float)) and 0 <= float(v) <= 10):
                    errors.append(_err(path, f"dims.{k} must be null or in [0, 10]"))
    cat = label.get("category")
    if cat is not None and cat not in ("best", "keep", "trash"):
        errors.append(_err(path, "category must be best|keep|trash|null"))
    keep = label.get("keep")
    if cat == "trash" and keep is True:
        errors.append(_err(path, "category=trash inconsistent with keep=true"))
    if cat == "best" and keep is False:
        errors.append(_err(path, "category=best inconsistent with keep=false"))
    return errors


def validate_golden_item(doc: dict[str, Any], path: str) -> list[str]:
    errors = _require(
        doc,
        ["schema_version", "item_id", "file", "content_hash", "label", "splits"],
        path,
    )
    if doc.get("schema_version") != "golden_item.v1":
        errors.append(_err(path, "schema_version must be golden_item.v1"))
    ch = doc.get("content_hash")
    if isinstance(ch, str) and not _SHA256.match(ch):
        errors.append(_err(path, "content_hash must be 64-char lowercase hex sha256"))
    splits = doc.get("splits")
    if isinstance(splits, list):
        if not splits:
            errors.append(_err(path, "splits must be non-empty"))
        bad = [s for s in splits if s not in _SPLITS]
        if bad:
            errors.append(_err(path, f"invalid splits: {bad}"))
    else:
        errors.append(_err(path, "splits must be array"))
    errors.extend(validate_label(doc.get("label"), f"{path}.label"))
    return errors


def validate_version_manifest(doc: dict[str, Any], path: str) -> list[str]:
    errors = _require(
        doc,
        [
            "schema_version",
            "manifest_id",
            "created_at",
            "prompt",
            "model",
            "workflow",
            "dataset",
            "code",
            "eval_protocol",
        ],
        path,
    )
    if doc.get("schema_version") != "version_manifest.v1":
        errors.append(_err(path, "schema_version must be version_manifest.v1"))
    prompt = doc.get("prompt") if isinstance(doc.get("prompt"), dict) else {}
    model = doc.get("model") if isinstance(doc.get("model"), dict) else {}
    workflow = doc.get("workflow") if isinstance(doc.get("workflow"), dict) else {}
    dataset = doc.get("dataset") if isinstance(doc.get("dataset"), dict) else {}
    code = doc.get("code") if isinstance(doc.get("code"), dict) else {}
    errors.extend(_require(prompt, ["prompt_id", "version", "content_hash"], f"{path}.prompt"))
    errors.extend(_require(model, ["provider", "model_name", "temperature"], f"{path}.model"))
    errors.extend(_require(workflow, ["workflow_id", "version", "config_hash"], f"{path}.workflow"))
    errors.extend(_require(dataset, ["name", "version"], f"{path}.dataset"))
    errors.extend(_require(code, ["git_sha", "dirty"], f"{path}.code"))
    for field, val in (
        ("prompt.content_hash", prompt.get("content_hash")),
        ("workflow.config_hash", workflow.get("config_hash")),
        ("version_manifest_hash", doc.get("version_manifest_hash")),
    ):
        if val is not None and not (isinstance(val, str) and _SHA256.match(val)):
            errors.append(_err(path, f"{field} must be 64-char lowercase hex sha256"))
    if model.get("provider") not in (None, "ollama", "vllm", "openai", "mock"):
        errors.append(_err(path, "model.provider invalid"))
    return errors


def validate_agent_chat_case(doc: dict[str, Any], path: str) -> list[str]:
    """Phase-0 Gallery agent utterance contract (``agent_chat_case.v1``)."""
    errors = _require(doc, ["schema_version", "id", "utterance", "split", "expect"], path)
    if doc.get("schema_version") != "agent_chat_case.v1":
        errors.append(_err(path, "schema_version must be agent_chat_case.v1"))
    if doc.get("split") not in _AGENT_CASE_SPLITS:
        errors.append(_err(path, f"split must be one of {sorted(_AGENT_CASE_SPLITS)}"))
    expect = doc.get("expect")
    if not isinstance(expect, dict):
        errors.append(_err(path, "expect must be object"))
    elif "route" in expect and expect["route"] is not None and not isinstance(expect["route"], str):
        errors.append(_err(path, "expect.route must be string or null"))
    tags = doc.get("tags")
    if isinstance(tags, list):
        bad = [t for t in tags if t not in _AGENT_CASE_TAGS]
        if bad:
            errors.append(_err(path, f"invalid tags: {bad}"))
    elif tags is not None:
        errors.append(_err(path, "tags must be array"))
    return errors


def validate_agent_rating(doc: dict[str, Any], path: str) -> list[str]:
    """Phase-6 human/LLM judge rating row (``agent_rating.v1``)."""
    errors = _require(
        doc,
        ["schema_version", "id", "case_id", "utterance", "reply", "scores", "rater"],
        path,
    )
    if doc.get("schema_version") != "agent_rating.v1":
        errors.append(_err(path, "schema_version must be agent_rating.v1"))
    for key in ("id", "case_id", "utterance", "reply", "rater"):
        val = doc.get(key)
        if key in doc and not (isinstance(val, str) and val.strip()):
            errors.append(_err(path, f"{key} must be non-empty string"))
    scores = doc.get("scores")
    if not isinstance(scores, dict):
        errors.append(_err(path, "scores must be object"))
    else:
        for key in ("useful", "honest", "concise"):
            if key not in scores:
                errors.append(_err(path, f"scores.{key} is required"))
                continue
            val = scores[key]
            if not (isinstance(val, int) and not isinstance(val, bool) and 1 <= val <= 5):
                errors.append(_err(path, f"scores.{key} must be int in [1, 5]"))
    tool_calls = doc.get("tool_calls")
    if tool_calls is not None:
        if not isinstance(tool_calls, list):
            errors.append(_err(path, "tool_calls must be array"))
        else:
            for i, tc in enumerate(tool_calls):
                if not isinstance(tc, dict):
                    errors.append(_err(path, f"tool_calls[{i}] must be object"))
                    continue
                if "tool" in tc and tc["tool"] is not None and not isinstance(tc["tool"], str):
                    errors.append(_err(path, f"tool_calls[{i}].tool must be string or null"))
                if "ok" in tc and tc["ok"] is not None and not isinstance(tc["ok"], bool):
                    errors.append(_err(path, f"tool_calls[{i}].ok must be bool or null"))
    if "pass" in doc and doc["pass"] is not None and not isinstance(doc["pass"], bool):
        errors.append(_err(path, "pass must be bool or null"))
    if "grounded_ok" in doc and doc["grounded_ok"] is not None and not isinstance(
        doc["grounded_ok"], bool
    ):
        errors.append(_err(path, "grounded_ok must be bool or null"))
    return errors


def validate_eval_run(doc: dict[str, Any], path: str) -> list[str]:
    errors = _require(
        doc,
        [
            "schema_version",
            "eval_run_id",
            "suite",
            "status",
            "started_at",
            "version_manifest",
            "dataset",
            "metrics",
            "artifact_root",
        ],
        path,
    )
    if doc.get("schema_version") != "eval_run.v1":
        errors.append(_err(path, "schema_version must be eval_run.v1"))
    if doc.get("suite") not in _SUITES:
        errors.append(_err(path, f"suite must be one of {sorted(_SUITES)}"))
    if doc.get("status") not in ("queued", "running", "succeeded", "failed", "cancelled"):
        errors.append(_err(path, "status invalid"))
    metrics = doc.get("metrics")
    if not isinstance(metrics, dict) or "schema" not in metrics:
        errors.append(_err(path, "metrics.schema is required"))
    vm = doc.get("version_manifest")
    if isinstance(vm, dict):
        if "schema_version" in vm:
            errors.extend(validate_version_manifest(vm, f"{path}.version_manifest"))
        else:
            errors.extend(
                _require(vm, ["manifest_id", "version_manifest_hash"], f"{path}.version_manifest")
            )
            h = vm.get("version_manifest_hash")
            if isinstance(h, str) and not _SHA256.match(h):
                errors.append(_err(path, "version_manifest_hash must be sha256 hex"))
    else:
        errors.append(_err(path, "version_manifest must be object"))
    return errors


_VALIDATORS = {
    "golden_item.v1": validate_golden_item,
    "version_manifest.v1": validate_version_manifest,
    "eval_run.v1": validate_eval_run,
    "agent_chat_case.v1": validate_agent_chat_case,
    "agent_rating.v1": validate_agent_rating,
}


def validate_document(doc: Any, path: str) -> list[str]:
    if not isinstance(doc, dict):
        return [_err(path, "document must be a JSON object")]
    schema_version = doc.get("schema_version")
    fn = _VALIDATORS.get(str(schema_version))
    if not fn:
        return [
            _err(
                path,
                f"unknown schema_version {schema_version!r}; "
                f"expected one of {sorted(_VALIDATORS)}",
            )
        ]
    return fn(doc, path)


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    paths = [Path(p) for p in argv[1:]]
    if not paths:
        paths = sorted(_EXAMPLES.glob("*.example.json"))
    if not paths:
        print("no documents to validate", file=sys.stderr)
        return 2
    n_err = 0
    for path in paths:
        try:
            doc = _load(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"FAIL {path}: {exc}")
            n_err += 1
            continue
        errors = validate_document(doc, str(path))
        if errors:
            print(f"FAIL {path}")
            for e in errors:
                print(f"  - {e}")
            n_err += len(errors)
        else:
            print(f"OK   {path}")
    return 1 if n_err else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
