"""Validity vs continuity: parse provenance rates and release gates.

Production may still emit neutral 5.0 / 55 fallbacks so the pipeline continues.
Eval must treat those as validity failures, not as ordinary photos.

Rates are computed only over items with a known parse status. Unknown historical
rows are reported as ``coverage`` and excluded from the gate denominator.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from inference.parsers import PARSE_FAIL, PARSE_REGEX, inspect_raw_dims
from utils.stage3_dimensions import STAGE3_DIM_KEYS

SCHEMA = "metrics.validity.v1"

DEFAULT_THRESHOLDS: dict[str, float] = {
    "parse_fail_rate": 0.02,
    "regex_recovery_rate": 0.05,
    "missing_dim_rate": 0.10,
}

_FAIL_OUTCOMES = frozenset(
    {"parse_failed", "fallback_defaults", "vlm_error", "exception"}
)
_RATE_KEYS = ("parse_fail_rate", "regex_recovery_rate", "missing_dim_rate")


def _as_meta(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    status = str(raw.get("status") or raw.get("parse_status") or "").strip()
    if not status:
        return None
    missing = raw.get("missing_dims") or []
    coerced = raw.get("coerced_dims") or []
    return {
        "status": status,
        "missing_dims": [str(x) for x in missing if x],
        "coerced_dims": [str(x) for x in coerced if x],
    }


def parse_meta_from_record(rec: Mapping[str, Any]) -> dict[str, Any] | None:
    """Best-effort parse provenance from a prediction / audit row."""
    direct = _as_meta(rec.get("parse_meta"))
    if direct:
        return direct
    stage3 = rec.get("stage3_meta") if isinstance(rec.get("stage3_meta"), Mapping) else {}
    from_meta = _as_meta(stage3)
    if from_meta:
        return from_meta
    if stage3.get("parse_status"):
        return {
            "status": str(stage3.get("parse_status")),
            "missing_dims": [str(x) for x in (stage3.get("missing_dims") or []) if x],
            "coerced_dims": [str(x) for x in (stage3.get("coerced_dims") or []) if x],
        }
    outcome = str(stage3.get("outcome") or "").strip()
    if outcome in _FAIL_OUTCOMES:
        return {
            "status": PARSE_FAIL,
            "missing_dims": list(STAGE3_DIM_KEYS),
            "coerced_dims": [],
        }
    if outcome == "success" and stage3.get("used_fallback_defaults"):
        return {
            "status": PARSE_FAIL,
            "missing_dims": list(STAGE3_DIM_KEYS),
            "coerced_dims": [],
        }
    return None


def summarize_validity(metas: Iterable[Mapping[str, Any] | None]) -> dict[str, Any]:
    """Aggregate parse_fail / regex_recovery / missing_dim rates."""
    known: list[Mapping[str, Any]] = []
    unknown = 0
    for meta in metas:
        if meta is None:
            unknown += 1
            continue
        status = str(meta.get("status") or "").strip()
        if not status:
            unknown += 1
            continue
        known.append(meta)

    n = len(known)
    n_fail = sum(1 for m in known if m.get("status") == PARSE_FAIL)
    n_regex = sum(1 for m in known if m.get("status") == PARSE_REGEX)
    n_missing = sum(1 for m in known if m.get("missing_dims"))
    missing_slots = sum(len(m.get("missing_dims") or []) for m in known)
    denom_slots = n * len(STAGE3_DIM_KEYS) if n else 0

    def _rate(count: int) -> float:
        return (count / n) if n else 0.0

    return {
        "schema": SCHEMA,
        "n_known": n,
        "n_unknown": unknown,
        "coverage": (n / (n + unknown)) if (n + unknown) else 0.0,
        "parse_fail": n_fail,
        "regex_recovery": n_regex,
        "missing_dim_items": n_missing,
        "parse_fail_rate": _rate(n_fail),
        "regex_recovery_rate": _rate(n_regex),
        "missing_dim_rate": _rate(n_missing),
        "missing_slot_rate": (missing_slots / denom_slots) if denom_slots else 0.0,
    }


def evaluate_validity_gate(
    validity: Mapping[str, Any],
    *,
    thresholds: Mapping[str, float] | None = None,
    baseline: Mapping[str, Any] | None = None,
    rise_eps: float = 1e-9,
) -> dict[str, Any]:
    """Absolute caps plus optional 'must not rise vs baseline' checks."""
    caps = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        for key, value in thresholds.items():
            try:
                caps[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

    checks: list[dict[str, Any]] = []
    failed = False
    if int(validity.get("n_known") or 0) <= 0:
        checks.append(
            {
                "id": "validity_coverage",
                "result": "skip",
                "metric": "n_known",
                "actual": 0,
                "threshold": 1,
                "message": "no parse provenance; validity gate skipped",
            }
        )
        return {"name": "validity", "result": "skip", "checks": checks}

    for key in _RATE_KEYS:
        actual = float(validity.get(key) or 0.0)
        cap = float(caps[key])
        ok = actual <= cap
        failed = failed or (not ok)
        checks.append(
            {
                "id": f"{key}_max",
                "result": "pass" if ok else "fail",
                "metric": key,
                "actual": actual,
                "threshold": cap,
                "message": f"{key} {actual:.4f} <= {cap:.4f}",
            }
        )
        if baseline is not None and baseline.get(key) is not None:
            prev = float(baseline[key])
            rose = actual > prev + rise_eps
            failed = failed or rose
            checks.append(
                {
                    "id": f"{key}_no_rise",
                    "result": "fail" if rose else "pass",
                    "metric": key,
                    "actual": actual,
                    "threshold": prev,
                    "message": f"{key} {actual:.4f} vs baseline {prev:.4f}",
                }
            )

    return {
        "name": "validity",
        "result": "fail" if failed else "pass",
        "checks": checks,
    }


def inspect_raw_json_validity(raw_obj: Any) -> dict[str, Any]:
    """Unit-test helper: dim presence on a parsed JSON object (no model)."""
    _, missing, coerced = inspect_raw_dims(raw_obj)
    return {
        "missing_dims": missing,
        "coerced_dims": coerced,
        "all_dims_missing": len(missing) == len(STAGE3_DIM_KEYS),
    }


def merge_gate(
    *gates: Optional[Mapping[str, Any]],
    name: str = "release",
) -> dict[str, Any]:
    """Combine validity / suite gates. skip-only → skip; any fail → fail."""
    checks: list[dict[str, Any]] = []
    results: list[str] = []
    for gate in gates:
        if not gate:
            continue
        results.append(str(gate.get("result") or "skip"))
        for row in gate.get("checks") or []:
            if isinstance(row, Mapping):
                checks.append(dict(row))
    if any(r == "fail" for r in results):
        result = "fail"
    elif any(r == "pass" for r in results):
        result = "pass"
    else:
        result = "skip"
    return {"name": name, "result": result, "checks": checks}
