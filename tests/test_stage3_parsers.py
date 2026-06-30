"""Unit tests for Stage3 VLM JSON extraction and dimensional parsing."""

from __future__ import annotations

import json

from inference.parsers import (
    clean_json_response,
    default_stage3_parsed,
    extract_first_json_object,
    parse_dimensional_response,
    parse_fast_vlm_response,
)
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def _minimal_valid_scores() -> dict:
    base = {k: float(i + 4) for i, k in enumerate(STAGE3_DIM_KEYS)}
    base.update(
        {
            "strongest_aspect": {"zh": "氛围", "en": "Atmosphere"},
            "weakest_aspect": {"zh": "噪点", "en": "Noise"},
            "tags": ["live"],
            "editing_suggestions": [{"zh": "裁切", "en": "Crop"}],
        }
    )
    return base


def test_clean_json_full_roundtrip():
    raw = json.dumps(_minimal_valid_scores())
    assert clean_json_response(raw) == raw


def test_concat_json_objects_first_only():
    a = _minimal_valid_scores()
    b = dict(a)
    b["tags"] = ["second"]
    blob = json.dumps(a) + "\n" + json.dumps(b)
    cleaned = clean_json_response(blob)
    data = json.loads(cleaned)
    assert data["tags"] == ["live"]


def test_extract_first_balanced_nested():
    inner = '{"x": {"y": 1}}'
    blob = 'noise {' + inner + "} trailing"
    assert extract_first_json_object(blob) == "{" + inner + "}"


def test_invalid_output_sentinel_returns_empty():
    s = '{"error":"invalid_output"}'
    assert parse_dimensional_response(s, s) == {}


def test_zh_only_mirrored_to_en():
    d = _minimal_valid_scores()
    d["strongest_aspect"] = {"zh": "仅此中文", "en": ""}
    d["weakest_aspect"] = {"zh": "", "en": "English only"}
    out = parse_dimensional_response(json.dumps(d), "")
    assert out["strongest_aspect"]["en"] == "仅此中文"
    assert out["weakest_aspect"]["zh"] == "English only"


def test_markdown_fence_stripped():
    obj = _minimal_valid_scores()
    raw = "```json\n" + json.dumps(obj) + "\n```"
    cleaned = clean_json_response(raw)
    assert parse_dimensional_response(cleaned, raw)["dimensions"]["focus_sharpness"] == 4.0


def test_default_stage3_shape():
    fb = default_stage3_parsed()
    assert len(fb["dimensions"]) == len(STAGE3_DIM_KEYS)
    assert all(fb["dimensions"][k] == 5.0 for k in STAGE3_DIM_KEYS)
    assert fb["strongest_aspect"] == {"zh": "", "en": ""}


def test_parse_fast_vlm_response_basic():
    raw = '{"score": 82, "verdict": "Nice moment.", "tags": ["stage", "crowd", "energy"]}'
    out = parse_fast_vlm_response(raw, raw)
    assert out["score"] == 82.0
    assert out["verdict"]["en"] == "Nice moment."
    assert out["tags"] == ["stage", "crowd", "energy"]
