"""Tests for quality.manifest (version_manifest.v1 bridge)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quality.manifest import (
    attach_manifest_hash,
    build_version_manifest,
    canonical_json_bytes,
    compact_manifest_ref,
    prompt_content_hash,
    sha256_hex,
    stamp_report_with_manifest,
    workflow_config_hash,
    write_version_manifest,
)
from quality.validate_contracts import validate_document
from scripts.eval.protocol import stamp_protocol

_REPO = Path(__file__).resolve().parents[1]
_EVAL_CFG = _REPO / "configs" / "eval_stage3.yaml"
_LABELS = _REPO / "data" / "eval" / "labels.jsonl"


@pytest.mark.skipif(not _EVAL_CFG.is_file(), reason="eval_stage3.yaml missing")
def test_build_version_manifest_validates():
    doc = build_version_manifest(
        config_path=_EVAL_CFG,
        labels_path=_LABELS if _LABELS.is_file() else None,
        manifest_id="test_manifest_unit",
        tags=["unit"],
    )
    assert doc["schema_version"] == "version_manifest.v1"
    assert doc["prompt"]["version"]
    assert len(doc["prompt"]["content_hash"]) == 64
    assert len(doc["workflow"]["config_hash"]) == 64
    assert len(doc["version_manifest_hash"]) == 64
    assert doc["model"]["temperature"] == 0.0
    assert doc["workflow"]["stage3_strategy"] == "full_only"
    assert doc["workflow"]["gating"] is False
    errors = validate_document(doc, "test_manifest")
    assert errors == [], errors


@pytest.mark.skipif(not _EVAL_CFG.is_file(), reason="eval_stage3.yaml missing")
def test_manifest_hash_stable_for_same_body():
    doc = build_version_manifest(
        config_path=_EVAL_CFG,
        manifest_id="stable_hash_test",
        created_at="2026-07-23T00:00:00+00:00",
        tags=["stable"],
    )
    h1 = doc["version_manifest_hash"]
    body = {k: v for k, v in doc.items() if k != "version_manifest_hash"}
    h2 = sha256_hex(canonical_json_bytes(body))
    assert h1 == h2
    again = attach_manifest_hash(dict(body))
    assert again["version_manifest_hash"] == h1


@pytest.mark.skipif(not _EVAL_CFG.is_file(), reason="eval_stage3.yaml missing")
def test_prompt_hash_changes_with_registry(monkeypatch):
    _pid, _ver, h0 = prompt_content_hash()
    import services.processor.stages.stage3_prompt_registry as reg

    monkeypatch.setitem(reg.PROMPT_BLOCKS, "domain", reg.PROMPT_BLOCKS["domain"] + "\n#probe\n")
    _pid2, _ver2, h1 = prompt_content_hash()
    assert h0 != h1


@pytest.mark.skipif(not _EVAL_CFG.is_file(), reason="eval_stage3.yaml missing")
def test_workflow_hash_tracks_strategy(tmp_path):
    import yaml

    raw = yaml.safe_load(_EVAL_CFG.read_text(encoding="utf-8"))
    h0 = workflow_config_hash(raw)
    raw = dict(raw)
    raw["stage3"] = dict(raw.get("stage3") or {})
    raw["stage3"]["strategy"] = "fast_first"
    h1 = workflow_config_hash(raw)
    assert h0 != h1


def test_stamp_protocol_attach_manifest(tmp_path):
    if not _EVAL_CFG.is_file():
        pytest.skip("eval_stage3.yaml missing")
    out = tmp_path / "version_manifest.json"
    report: dict = {"overall": {"n": 0}}
    stamp_protocol(
        report,
        labels_path=_LABELS if _LABELS.is_file() else None,
        config_path=_EVAL_CFG,
        attach_manifest=True,
        manifest_out=out,
    )
    assert out.is_file()
    assert "version_manifest" in report
    ref = report["version_manifest"]
    assert ref["manifest_id"]
    assert len(ref["version_manifest_hash"]) == 64
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert validate_document(loaded, str(out)) == []
    assert report["protocol"]["extra"]["version_manifest"]["version_manifest_hash"] == (
        loaded["version_manifest_hash"]
    )


def test_compact_ref_and_write(tmp_path):
    if not _EVAL_CFG.is_file():
        pytest.skip("eval_stage3.yaml missing")
    doc = build_version_manifest(config_path=_EVAL_CFG, manifest_id="write_test")
    path = write_version_manifest(tmp_path / "m.json", doc)
    ref = compact_manifest_ref(doc)
    report: dict = {"protocol": {"extra": {}}}
    stamp_report_with_manifest(report, doc)
    assert report["version_manifest"] == ref
    assert json.loads(path.read_text(encoding="utf-8"))["manifest_id"] == "write_test"
