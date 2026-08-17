"""Prompt builder for livehouse Stage3 dimensional analysis."""
from __future__ import annotations

from typing import Any

from services.processor.stages.stage1_signal_semantics import stage1_semantic_lines
from services.processor.stages.stage3_prompt_registry import (
    PROMPT_VERSION,
    compose_stage3_defect_head_prompt,
    compose_stage3_semantic_compact_prompt,
    compose_stage3_fast_prompt,
    compose_stage3_full_prompt,
    compose_stage3_semantic_first_prompt,
)

# Re-export for benchmarks, meta, and A/B hooks.
STAGE3_PROMPT_VERSION = PROMPT_VERSION


def stage1_compact_line(stage1: dict[str, Any] | None) -> str:
    """Turn Stage1 numbers into one English hint line for the VLM prompt.

    Example::

        stage1_compact_line({
            "laplacian_var": 80,
            "edge_ratio": 0.02,
            "highlight_frac": 0.30,
            "blur_type": "motion_blur",
        })
        # -> "Image signals: sharpness=moderate sharpness (laplacian_var=80); "
        #    "edge_structure=sparse edges (edge_ratio=0.020); "
        #    "highlights=highlight clipping risk (highlight_frac=0.30); "
        #    "blur_type=motion_blur.\\n"

        stage1_compact_line(None)  # -> ""
    """
    return stage1_semantic_lines(stage1)


def build_stage3_fast_prompt(*, blur_eff: str | None, stage1_features: dict[str, Any] | None) -> str:
    """Production fast-pass prompt: one 0–100 score + tags + semantic_gate (no 8 dims).

    Example::

        build_stage3_fast_prompt(
            blur_eff="motion_blur",
            stage1_features={"laplacian_var": 80, "edge_ratio": 0.02},
        )
        # -> long English prompt that:
        #    - asks for JSON {score, verdict, dimensions:{}, tags, mood_tags, semantic_gate}
        #    - appends "Image signals: sharpness=moderate sharpness ..."
        #    - appends "Note: possible motion blur."
    """
    return compose_stage3_fast_prompt(
        blur_eff=blur_eff,
        stage1_features=stage1_features,
        stage1_line_fn=stage1_semantic_lines,
    )


def build_stage3_semantic_first_prompt() -> str:
    """A/B prompt: inspect semantic_gate first, then score. Not used in production routing.

    Example::

        build_stage3_semantic_first_prompt()
        # -> English prompt whose JSON starts with semantic_gate, then score/verdict/tags.
        #    No Stage1 line, no blur note (no arguments).
    """
    return compose_stage3_semantic_first_prompt()


def build_stage3_defect_head_prompt() -> str:
    """Standalone defect head for A/B. Not used in the production full pass."""
    return compose_stage3_defect_head_prompt()


def build_stage3_semantic_compact_prompt() -> str:
    """A/B prompt: short Chinese gate-first contract for small local VLMs.

    Example::

        build_stage3_semantic_compact_prompt()
        # -> "你是 Livehouse 演出摄影质检员。..." plus a compact JSON schema
        #    {semantic_gate, score, verdict, tags, mood_tags}.
    """
    return compose_stage3_semantic_compact_prompt()


def build_stage3_prompt(
    *,
    blur_eff: str | None,
    stage1_features: dict[str, Any] | None,
    strict_retry: bool = False,
) -> str:
    """Production full-pass prompt: 8 dimension scores + bilingual aspects + semantic_gate.

    Example::

        build_stage3_prompt(
            blur_eff="artistic_motion_blur",
            stage1_features={"laplacian_var": 180, "edge_ratio": 0.09},
            strict_retry=False,
        )
        # -> domain + JSON contract + 8-dim rubric + placeholder exemplar
        #    + "Image signals: sharpness=good sharpness ..."
        #    + "Note: artistic motion blur is acceptable when the moment reads clearly."

        build_stage3_prompt(blur_eff=None, stage1_features=None, strict_retry=True)
        # -> same contract without the filled-shape exemplar;
        #    ends with "Previous output was invalid JSON. Re-emit corrected JSON only..."
    """
    return compose_stage3_full_prompt(
        blur_eff=blur_eff,
        stage1_features=stage1_features,
        stage1_line_fn=stage1_semantic_lines,
        strict_retry=strict_retry,
        include_exemplar=not strict_retry,
    )
