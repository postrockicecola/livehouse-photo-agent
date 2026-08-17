"""Validity provenance: parse_meta stamps and release gates."""
from __future__ import annotations

import json

from inference.parsers import (
    PARSE_FAIL,
    PARSE_OK,
    PARSE_REGEX,
    default_stage3_parsed,
    parse_dimensional_response,
)
from quality.validity import evaluate_validity_gate, summarize_validity
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _full_scores() -> dict:
    return {key: float(i + 3) for i, key in enumerate(STAGE3_DIM_KEYS)}


def test_valid_json_stamps_ok_meta() -> None:
    payload = {
        "dimensions": _full_scores(),
        "strongest_aspect": {"zh": "瞬间", "en": "moment"},
        "weakest_aspect": {"zh": "噪点", "en": "noise"},
        "tags": ["stage"],
    }
    out = parse_dimensional_response(json.dumps(payload), "")
    assert out["parse_meta"]["status"] == PARSE_OK
    assert out["parse_meta"]["missing_dims"] == []


def test_all_dims_missing_is_hard_fail() -> None:
    out = parse_dimensional_response('{"tags":["stage"],"strongest_aspect":"x"}', "")
    assert out == {}


def test_partial_dims_keep_scores_but_record_missing() -> None:
    dims = _full_scores()
    del dims["exposure_control"]
    payload = {
        "dimensions": dims,
        "strongest_aspect": "ok",
        "weakest_aspect": "ok",
        "tags": ["stage"],
    }
    out = parse_dimensional_response(json.dumps(payload), "")
    assert out["parse_meta"]["status"] == PARSE_OK
    assert out["parse_meta"]["missing_dims"] == ["exposure_control"]
    assert out["dimensions"]["exposure_control"] == 5.0


def test_regex_recovery_is_not_ok() -> None:
    body = ",".join(f'"{k}": {i + 1}' for i, k in enumerate(STAGE3_DIM_KEYS))
    truncated = "{" + body + ", \"strongest_aspect\": \"x\""
    out = parse_dimensional_response(truncated, truncated)
    assert out
    assert out["parse_meta"]["status"] == PARSE_REGEX


def test_default_fallback_is_fail() -> None:
    fb = default_stage3_parsed()
    assert fb["parse_meta"]["status"] == PARSE_FAIL
    assert fb["parse_meta"]["missing_dims"] == list(STAGE3_DIM_KEYS)


def test_validity_gate_fails_when_rates_rise() -> None:
    validity = summarize_validity(
        [
            {"status": PARSE_FAIL, "missing_dims": list(STAGE3_DIM_KEYS)},
            {"status": PARSE_OK, "missing_dims": []},
        ]
    )
    assert validity["parse_fail_rate"] == 0.5
    gate = evaluate_validity_gate(validity)
    assert gate["result"] == "fail"
    ids = {row["id"] for row in gate["checks"] if row["result"] == "fail"}
    assert "parse_fail_rate_max" in ids


def test_validity_gate_skips_without_provenance() -> None:
    gate = evaluate_validity_gate(summarize_validity([None, None]))
    assert gate["result"] == "skip"


def test_validity_gate_detects_rise_vs_baseline() -> None:
    current = {
        "n_known": 10,
        "parse_fail_rate": 0.01,
        "regex_recovery_rate": 0.0,
        "missing_dim_rate": 0.0,
    }
    baseline = {
        "parse_fail_rate": 0.0,
        "regex_recovery_rate": 0.0,
        "missing_dim_rate": 0.0,
    }
    gate = evaluate_validity_gate(current, baseline=baseline)
    assert gate["result"] == "fail"
    assert any(row["id"] == "parse_fail_rate_no_rise" for row in gate["checks"])
