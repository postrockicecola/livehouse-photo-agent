"""Deterministic policy for Stage3 semantic-defect observations."""
from __future__ import annotations

from typing import Any, Mapping


SEMANTIC_DEFECT_TYPES = frozenset(
    {
        "heavy_occlusion",
        "closed_eyes",
        "no_clear_subject",
        "missed_moment",
        "severe_composition_failure",
        "bad_expression",
        "invalid_pose",
        "other",
    }
)
SEMANTIC_GATE_MODES = frozenset({"off", "observe", "soft", "hard"})
_SUBJECTIVE_TYPES = frozenset(
    {"closed_eyes", "missed_moment", "bad_expression", "invalid_pose", "other"}
)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "present"}
    return bool(value)


def sanitize_semantic_observation(raw: Any) -> dict[str, Any] | None:
    """Normalize an optional model observation without making a policy decision."""
    if not isinstance(raw, Mapping):
        return None
    types: list[str] = []
    for value in raw.get("types") or []:
        defect_type = str(value).strip().lower()
        if defect_type in SEMANTIC_DEFECT_TYPES and defect_type not in types:
            types.append(defect_type)
    try:
        severity = max(0, min(3, int(round(float(raw.get("severity") or 0)))))
    except (TypeError, ValueError):
        severity = 0
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    evidence = str(raw.get("evidence") or "").strip()[:500]
    return {
        "is_present": _coerce_bool(raw.get("is_present")),
        "types": types,
        "severity": severity,
        "confidence": round(confidence, 4),
        "evidence": evidence,
    }


def semantic_gate_settings(config: Mapping[str, Any]) -> dict[str, Any]:
    raw = ((config.get("stage3") or {}).get("semantic_gate") or {})
    mode = str(raw.get("mode") or "off").strip().lower()
    if mode not in SEMANTIC_GATE_MODES:
        mode = "off"
    return {
        "enabled": bool(raw.get("enabled", False)),
        "mode": mode,
        "min_clear_confidence": float(raw.get("min_clear_confidence", 0.65)),
        "default_min_severity": int(raw.get("default_min_severity", 2)),
        "default_min_confidence": float(raw.get("default_min_confidence", 0.80)),
        "subjective_min_severity": int(raw.get("subjective_min_severity", 3)),
        "subjective_min_confidence": float(raw.get("subjective_min_confidence", 0.90)),
    }


def evaluate_semantic_gate(
    observation: Any,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn model evidence into pass/reject/review/unknown via fixed policy."""
    settings = semantic_gate_settings(config)
    normalized = sanitize_semantic_observation(observation)
    base = normalized or {
        "is_present": False,
        "types": [],
        "severity": 0,
        "confidence": 0.0,
        "evidence": "",
    }
    if not settings["enabled"] or settings["mode"] == "off":
        status = "disabled"
    elif normalized is None:
        status = "unknown"
    elif not base["is_present"]:
        if base["types"] or base["severity"] or base["evidence"]:
            status = "review"
        else:
            status = (
                "pass"
                if base["confidence"] >= settings["min_clear_confidence"]
                else "unknown"
            )
    else:
        has_objective = bool(set(base["types"]) - _SUBJECTIVE_TYPES)
        min_severity = (
            settings["default_min_severity"]
            if has_objective
            else settings["subjective_min_severity"]
        )
        min_confidence = (
            settings["default_min_confidence"]
            if has_objective
            else settings["subjective_min_confidence"]
        )
        reject = (
            bool(base["types"])
            and bool(base["evidence"])
            and base["severity"] >= min_severity
            and base["confidence"] >= min_confidence
        )
        status = "reject" if reject else "review"
    return {
        **base,
        "status": status,
        "mode": settings["mode"],
        "policy_version": "semantic_gate.v1",
    }


def apply_semantic_gate_policy(
    result: Mapping[str, Any],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Attach a decision and operational tags without changing aesthetic score."""
    out = dict(result)
    if "semantic_gate" not in out and not out.get("stage3_meta"):
        return out
    decision = evaluate_semantic_gate(out.get("semantic_gate"), config)
    out["semantic_gate"] = decision
    tags = [str(tag) for tag in out.get("tags") or []]
    if decision["mode"] in {"soft", "hard"}:
        marker = (
            "semantic_reject"
            if decision["status"] == "reject"
            else "semantic_gate_unresolved"
            if decision["status"] in {"unknown", "review"}
            else ""
        )
        if marker and marker not in tags:
            tags.append(marker)
    out["tags"] = tags
    return out
