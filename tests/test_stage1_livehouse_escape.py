"""Stage1 severe-blur livehouse escape hatch."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.operators.image_processor import ImageProcessor, livehouse_severe_blur_escape
from utils.config_loader import ConfigLoader

_EVAL_ROOT = Path(__file__).resolve().parents[1] / "data" / "eval" / "images"


@pytest.fixture(scope="module")
def livehouse_q():
    cfg = ConfigLoader.load("configs/livehouse.yaml")
    return ConfigLoader.get_quality_thresholds(cfg)


def test_haze_escape_metrics_match_eval_keepers(livehouse_q) -> None:
    """Regression: two human-60 frames previously hard-rejected as severe blur."""
    cases = [
        ("20260328__DSC09078.jpg", "livehouse_haze"),
        ("20260328__DSC09080.jpg", "livehouse_haze"),
    ]
    for name, expected_escape in cases:
        path = _EVAL_ROOT / name
        if not path.is_file():
            pytest.skip(f"eval image missing: {path}")
        ok, reason, _tech, dbg = ImageProcessor.assess_image_quality(str(path), livehouse_q)
        assert ok, reason
        assert dbg.get("stage1_severe_blur_escape") == expected_escape
        assert dbg.get("blur_type") == "artistic_motion_blur"


def test_true_trash_still_rejects_severe_blur(livehouse_q) -> None:
    path = _EVAL_ROOT / "20260313__DSC06178.jpg"
    if not path.is_file():
        pytest.skip(f"eval image missing: {path}")
    ok, reason, _tech, _dbg = ImageProcessor.assess_image_quality(str(path), livehouse_q)
    assert not ok
    assert reason and "Severe blur" in reason


def test_livehouse_haze_escape_pure_function(livehouse_q) -> None:
    expo = {"p50": 14.0, "highlight_frac": 0.0, "shadow_frac": 0.0, "p01": 3.0, "p99": 35.0}
    reason = livehouse_severe_blur_escape(
        laplacian_var=0.8,
        edge_ratio=0.000174,
        grad_extreme=False,
        contrast=5.3,
        expo=expo,
        q=livehouse_q,
    )
    assert reason == "livehouse_haze"
