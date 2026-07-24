"""Stage2 ambiguous soft-gates + Stage3 stratified admission."""
from __future__ import annotations

from engine.operators.stage2_prefilter import row_ambiguous_tags
from services.processor.pipeline_image_ops import apply_stage3_candidates_gating
from services.processor.stages.deep_analysis import should_run_full_after_fast


def _row(name: str, tech: float, fast: float, *, ambiguous: list[str] | None = None) -> dict:
    dbg: dict = {}
    if ambiguous:
        dbg["ambiguous_tags"] = list(ambiguous)
    return {
        "file_name": name,
        "tech_score": tech,
        "fast_score": fast,
        "debug_info": dbg,
        "phash": 0,
    }


def test_row_ambiguous_tags_from_debug_info():
    r = _row("a.jpg", 50, 50, ambiguous=["motion_blur", "no_face"])
    assert row_ambiguous_tags(r) == ["motion_blur", "no_face"]
    assert row_ambiguous_tags({"file_name": "b.jpg"}) == []


def test_ambiguous_reserve_admits_low_score_motion_frame():
    # High-scoring clean frames + one low-scoring ambiguous motion keeper.
    rows = [_row(f"hi_{i}.jpg", 90 - i, 88 - i) for i in range(10)]
    rows.append(_row("motion_keeper.jpg", 45, 40, ambiguous=["motion_blur", "soft_blur"]))

    cfg = {
        "processing": {
            "stage3_gating": {
                "dynamic_batch_gating": False,
                "stage3_threshold": 0.50,
                "top_k_ratio": 1.0,
                "max_candidates": 6,
                "ambiguous_reserve": 2,
            }
        }
    }
    kept, skipped, diag = apply_stage3_candidates_gating(rows, config=cfg)
    names = {r["file_name"] for r in kept}
    assert "motion_keeper.jpg" in names
    assert diag["after"] == 6
    assert diag["ambiguous_admitted"] >= 1
    assert len(skipped) == len(rows) - 6


def test_should_run_full_for_ambiguous_debug_info():
    fast = {"score": 62, "tags": ["gel lighting"]}
    assert should_run_full_after_fast(fast, debug_info={"ambiguous_tags": ["no_face"]})
    assert should_run_full_after_fast(fast, debug_info={"blur_type": "motion_blur"})
    assert should_run_full_after_fast(
        fast, debug_info={"contrast": 8.0, "edge_ratio": 0.003}
    )
    assert not should_run_full_after_fast(fast, debug_info={"blur_type": "none", "contrast": 30})
