"""Behavioral-contract and photography-golden loading."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

_REQUIRED = ("id", "description", "user_input")
_BUDGET_KEYS = (
    "max_steps",
    "max_tool_calls",
    "max_llm_calls",
    "max_inference_calls",
    "max_tokens",
    "max_latency_ms",
)


def _documents(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
    else:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        for collection_key in ("cases", "goldens"):
            if isinstance(value.get(collection_key), list):
                value = value[collection_key]
                break
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"{path}: expected a case object or list of case objects")
    return value


def normalize_case(case: dict[str, Any]) -> dict[str, Any]:
    """Normalize the v1 exact-tool schema into a v2 behavioral contract.

    Legacy fields remain accepted so existing personal benchmark files do not
    break. Their ``expected_tools`` become non-blocking preferred tools.
    """
    row = dict(case)
    required = dict(row.get("required_behavior") or {})
    optional = dict(row.get("optional_behavior") or {})
    if "expected_intent" in row:
        required.setdefault("intent", row["expected_intent"])
    if "expected_result" in row:
        required.setdefault("final_answer", row["expected_result"])
    budgets = dict(required.get("budgets") or {})
    for key in _BUDGET_KEYS:
        if key in row:
            budgets.setdefault(key, row[key])
    if budgets:
        required["budgets"] = budgets
    if "expected_tools" in row:
        optional.setdefault("preferred_tools", list(row.get("expected_tools") or []))
    if "expected_selected_images" in row:
        row.setdefault(
            "golden",
            {"expected_images": list(row.get("expected_selected_images") or [])},
        )
    row["schema_version"] = str(row.get("schema_version") or "agent_behavior_case.v2")
    row["required_behavior"] = required
    row["optional_behavior"] = optional
    return row


def validate_case(case: dict[str, Any], source: str) -> None:
    missing = [key for key in _REQUIRED if key not in case]
    if missing:
        raise ValueError(f"{source}: missing required fields {missing}")
    if not str(case["id"]).strip() or not str(case["user_input"]).strip():
        raise ValueError(f"{source}: id and user_input must be non-empty")
    required = case.get("required_behavior")
    optional = case.get("optional_behavior")
    if not isinstance(required, dict):
        raise ValueError(f"{source}: required_behavior must be an object")
    if not isinstance(optional, dict):
        raise ValueError(f"{source}: optional_behavior must be an object")
    preferred = optional.get("preferred_tools")
    if preferred is not None and (
        not isinstance(preferred, list)
        or not all(isinstance(tool, str) and tool for tool in preferred)
    ):
        raise ValueError(f"{source}: optional_behavior.preferred_tools must be string[]")
    budgets = required.get("budgets") or {}
    if not isinstance(budgets, dict):
        raise ValueError(f"{source}: required_behavior.budgets must be an object")
    for key in _BUDGET_KEYS:
        if key in budgets and (
            not isinstance(budgets[key], (int, float)) or budgets[key] < 0
        ):
            raise ValueError(f"{source}: required_behavior.budgets.{key} must be non-negative")
    golden = case.get("golden")
    if golden is not None:
        images = golden.get("expected_images") if isinstance(golden, dict) else None
        if not isinstance(images, list) or not all(isinstance(item, str) for item in images):
            raise ValueError(f"{source}: golden.expected_images must be string[]")


def load_cases(cases_dir: Path, *, case_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Load deterministic YAML/JSON cases in filename and document order."""
    paths = sorted(
        path
        for path in cases_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml", ".json"}
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        for index, row in enumerate(_documents(path), start=1):
            source = f"{path}:{index}"
            normalized = normalize_case(row)
            validate_case(normalized, source)
            case_id = str(normalized["id"])
            if case_id in seen:
                raise ValueError(f"{source}: duplicate case id {case_id!r}")
            seen.add(case_id)
            if case_ids is None or case_id in case_ids:
                rows.append(normalized)
    if not rows:
        raise ValueError(f"no benchmark cases found in {cases_dir}")
    return rows


def load_golden_cases(golden_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Load selection goldens keyed by behavior-case id.

    A golden is intentionally a separate dataset: it grades image ranking, not
    whether the agent completed its workflow.
    """
    if golden_dir is None or not golden_dir.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(
        p
        for p in golden_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".yaml", ".yml", ".json"}
    ):
        for index, row in enumerate(_documents(path), start=1):
            case_id = str(row.get("case_id") or row.get("case") or row.get("id") or "").strip()
            images = row.get("expected_images")
            if not case_id:
                raise ValueError(f"{path}:{index}: golden case_id is required")
            if not isinstance(images, list) or not all(isinstance(item, str) for item in images):
                raise ValueError(f"{path}:{index}: expected_images must be string[]")
            if case_id in out:
                raise ValueError(f"{path}:{index}: duplicate golden case {case_id!r}")
            out[case_id] = {
                "expected_images": list(images),
                "relevance": dict(row.get("relevance") or {}),
                "k": int(row.get("k") or len(images) or 10),
            }
    return out

