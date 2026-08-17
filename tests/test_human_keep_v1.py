"""human_keep_v1 assemble: deliverable keep from selection_v1 sample_type."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.eval.build_human_keep_v1 import build

_DATASET = Path(__file__).resolve().parents[1] / "data" / "eval" / "selection_v1"


@pytest.mark.skipif(
    not (_DATASET / "frozen_manifest.json").is_file(),
    reason="selection_v1 freeze not present",
)
def test_human_keep_uses_sample_type_not_highlight_only(tmp_path: Path) -> None:
    manifest = build(_DATASET, tmp_path, seed=20260817, irr_n=100)
    assert manifest["n_labels"] == 250
    assert manifest["n_keep"] == 150
    assert manifest["n_drop"] == 100
    assert manifest["irr_n"] == 100
    assert manifest["counts"]["ordinary"] == 100
    assert manifest["counts"]["highlight"] == 50
