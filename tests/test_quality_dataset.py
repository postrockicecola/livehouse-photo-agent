"""Tests for quality.dataset golden hydration."""
from __future__ import annotations

from pathlib import Path

import pytest

from quality.dataset import (
    filter_by_split,
    hydrate_golden_items,
    load_dataset_registry,
    load_media_index,
)
from quality.validate_contracts import validate_document

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE = _REPO / "quality" / "fixtures" / "smoke"
_LABELS = _REPO / "data" / "eval" / "labels.jsonl"
_MANIFEST = _REPO / "data" / "eval" / "manifest.json"


def test_load_registry_defaults():
    reg = load_dataset_registry()
    assert reg["name"] == "golden_core"
    assert reg["version"]
    assert int(reg.get("smoke_limit") or 0) >= 1


def test_hydrate_smoke_fixture():
    items, errors = hydrate_golden_items(
        _FIXTURE / "labels.jsonl",
        _FIXTURE / "manifest.json",
        smoke_limit=8,
    )
    assert errors == []
    assert len(items) == 8
    smoke = filter_by_split(items, "smoke")
    assert len(smoke) == 8
    for item in items:
        assert validate_document(item, item["item_id"]) == []
        assert item["content_hash"]
        assert item["label"].get("overall") is not None


@pytest.mark.skipif(not _LABELS.is_file() or not _MANIFEST.is_file(), reason="golden_core missing")
def test_hydrate_golden_core_sample():
    index = load_media_index(_MANIFEST)
    assert len(index) >= 10
    items, errors = hydrate_golden_items(
        _LABELS,
        _MANIFEST,
        smoke_limit=16,
        skip_missing_hash=True,
    )
    assert len(items) >= 16
    # First 16 should carry smoke split.
    smoke = filter_by_split(items, "smoke")
    assert len(smoke) == 16
    for item in items[:32]:
        assert validate_document(item, item["item_id"]) == [], item["item_id"]
    # Missing-hash skips should be rare on the tracked 250-set.
    assert len(errors) == 0
