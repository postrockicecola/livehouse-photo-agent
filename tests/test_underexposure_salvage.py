"""Underexposure salvage lane: Stage1 rebate + Stage2 gate relief."""
from __future__ import annotations

import numpy as np

from engine.operators.underexposure_salvage import (
    evaluate_underexposure_salvage,
    merge_salvage_config,
    stage2_gate_scores,
)
from services.processor.pipeline_image_ops import passes_stage2_thresholds


def _dark_structured_gray(h: int = 240, w: int = 320) -> np.ndarray:
    """Low midtone but with edges / contrast (salvageable underexposure)."""
    g = np.full((h, w), 18.0, dtype=np.float32)
    # Soft gradient + a brighter subject blob for dynamic range / edges.
    xs = np.linspace(0, 1, w, dtype=np.float32)
    g += xs[None, :] * 22.0
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    blob = ((yy - cy) ** 2 + (xx - cx) ** 2) < (min(h, w) * 0.18) ** 2
    g[blob] = 55.0
    # Checker strip for Sobel edges after EV lift.
    g[::8, :] = np.minimum(g[::8, :] + 28.0, 80.0)
    return np.clip(g, 0, 255).astype(np.uint8)


def _flat_near_black(h: int = 240, w: int = 320) -> np.ndarray:
    return np.full((h, w), 4, dtype=np.uint8)


def test_merge_processing_overrides_defaults() -> None:
    cfg = merge_salvage_config(
        {},
        {"underexposure_salvage": {"enabled": True, "tech_rebate_cap": 12.0}},
    )
    assert float(cfg["tech_rebate_cap"]) == 12.0
    assert float(cfg["enabled"]) == 1.0


def test_salvage_accepts_dark_structured_frame() -> None:
    gray = _dark_structured_gray()
    expo = {
        "p01": 8.0,
        "p50": 22.0,
        "p99": 58.0,
        "highlight_frac": 0.01,
        "shadow_frac": 0.72,
    }
    out = evaluate_underexposure_salvage(
        gray=gray,
        penalties=["shadow_heavy", "brightness_low_soft"],
        expo=expo,
        edge_ratio=0.01,
        contrast=14.0,
        blur_type="none",
        tech_score=28.0,
        cfg=merge_salvage_config({}, {"underexposure_salvage": {"enabled": True}}),
    )
    assert out["underexposure_salvage"] is True
    assert out["effective_tech_score"] > 28.0
    assert out["salvage_ev"] > 0.0


def test_salvage_skips_without_shadow_penalty() -> None:
    gray = _dark_structured_gray()
    out = evaluate_underexposure_salvage(
        gray=gray,
        penalties=["laplacian_low_soft"],
        expo={"p01": 8.0, "p50": 22.0, "p99": 58.0, "highlight_frac": 0.01, "shadow_frac": 0.2},
        edge_ratio=0.01,
        contrast=14.0,
        blur_type="none",
        tech_score=40.0,
    )
    assert out["underexposure_salvage"] is False
    assert out["salvage_skip_reason"] == "no_shadow_penalty"


def test_salvage_skips_dead_dynamic_range() -> None:
    gray = _flat_near_black()
    out = evaluate_underexposure_salvage(
        gray=gray,
        penalties=["shadow_heavy"],
        expo={"p01": 2.0, "p50": 4.0, "p99": 6.0, "highlight_frac": 0.0, "shadow_frac": 0.9},
        edge_ratio=0.01,
        contrast=8.0,
        blur_type="none",
        tech_score=20.0,
    )
    assert out["underexposure_salvage"] is False
    assert out["salvage_skip_reason"] == "dynamic_range_dead"


def test_stage2_gate_uses_effective_tech_and_fast_floor() -> None:
    tech_used, fast_used, tech_min, fast_min = stage2_gate_scores(
        28.0,
        18.0,
        debug_info={
            "underexposure_salvage": True,
            "effective_tech_score": 45.0,
            "salvage_fast_score_min": 16.0,
        },
        tech_score_min=32.0,
        fast_aesthetic_score_min=22.0,
    )
    assert tech_used == 45.0
    assert fast_used == 18.0
    assert tech_min == 32.0
    assert fast_min == 16.0


def test_passes_stage2_thresholds_with_salvage_debug() -> None:
    cfg = {
        "fast_aesthetic": {"tech_score_min": 32, "fast_aesthetic_score_min": 22},
    }
    assert not passes_stage2_thresholds(cfg, 28.0, 18.0)
    assert passes_stage2_thresholds(
        cfg,
        28.0,
        18.0,
        debug_info={
            "underexposure_salvage": True,
            "effective_tech_score": 45.0,
            "salvage_fast_score_min": 16.0,
        },
    )


def test_dark_ev_escape_recovers_crushed_structure() -> None:
    from engine.operators.underexposure_salvage import try_dark_ev_structure_escape

    # Same pattern as DSC09685: p50≈3, tiny native edges, subject returns after EV.
    h, w = 240, 320
    g = np.full((h, w), 2, dtype=np.uint8)
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    g[((yy - cy) ** 2 + (xx - cx) ** 2) < 40**2] = 28
    g[::6, :] = np.minimum(g[::6, :] + 10, 40)
    expo = {"p01": 1.0, "p50": 3.0, "p99": 33.0, "highlight_frac": 0.0, "shadow_frac": 0.0}
    esc = try_dark_ev_structure_escape(
        gray=g,
        expo=expo,
        edge_ratio=0.0009,
        cfg=merge_salvage_config({}, {"underexposure_salvage": {"enabled": True, "ev_max": 6.5}}),
    )
    assert esc is not None
    assert esc["reason"] == "underexposure_ev_structure"
    assert float(esc["salvage_ev"]) >= 4.0


def test_dsc09685_severe_blur_escapes_via_underexposure() -> None:
    from pathlib import Path

    from engine.operators.image_processor import ImageProcessor
    from utils.config_loader import ConfigLoader

    path = Path("/Volumes/M4Buffer/Visions/Livehouse_Archive/2026-07-29/Previews/DSC09685.jpg")
    if not path.is_file():
        import pytest

        pytest.skip(f"missing fixture image: {path}")

    cfg = ConfigLoader.load("configs/livehouse.yaml")
    q = dict(ConfigLoader.get_quality_thresholds(cfg) or {})
    proc = (cfg.get("processing") or {}).get("underexposure_salvage")
    if isinstance(proc, dict):
        q["underexposure_salvage"] = proc

    ok, reason, tech, dbg = ImageProcessor.assess_image_quality(str(path), q)
    assert ok is True, reason
    assert dbg.get("stage1_severe_blur_escape") == "underexposure_ev_structure"
    assert dbg.get("underexposure_salvage") is True
    assert float(dbg.get("effective_tech_score") or 0.0) >= 32.0
    assert tech > 0.0


def test_near_black_structured_frame_gets_high_display_ev(tmp_path) -> None:
    """p99 under black_p99_max used to hard-reject before salvage; escape + high EV."""
    from PIL import Image

    from engine.operators.image_processor import ImageProcessor

    # Dark stage frame: midtones crushed, sparse bright speckles (like DSC09728).
    # Need p99<=22 (near-black) but p99-p01>=12 so structure escape fires.
    rng = np.random.default_rng(0)
    h, w = 480, 640
    g = np.full((h, w), 1, dtype=np.uint8)
    g[100:380, 120:520] = 14
    g[::5, :] = 10
    n = int(h * w * 0.004)
    g[rng.integers(0, h, n), rng.integers(0, w, n)] = 210
    # PNG avoids JPEG ringing that can push p99 above black_p99_max.
    path = tmp_path / "near_black.png"
    Image.fromarray(np.stack([g, g, g], axis=-1), mode="RGB").save(path)

    ok, reason, tech, dbg = ImageProcessor.assess_image_quality(
        str(path),
        {
            "underexposure_salvage": {
                "enabled": True,
                "ev_max": 6.5,
                "min_contrast": 4.0,
                "min_edge_ratio": 0.0015,
                "min_p_range": 12.0,
            }
        },
    )
    assert ok is True
    assert reason is None
    assert float(dbg.get("p99") or 0.0) <= 22.0
    assert dbg.get("stage1_near_black_escape") == "near_black_residual_structure"
    assert dbg.get("underexposure_salvage") is True
    assert float(dbg.get("salvage_ev") or 0.0) >= 4.0
    assert tech > 0.0
