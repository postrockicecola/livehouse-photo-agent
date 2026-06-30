"""Prompt builder for livehouse Stage3 dimensional analysis."""
from __future__ import annotations

from typing import Any

from services.processor.stages.stage1_signal_semantics import stage1_semantic_lines
from services.processor.stages.stage3_prompt_registry import (
    PROMPT_VERSION,
    compose_stage3_fast_prompt,
    compose_stage3_full_prompt,
)

# Re-export for benchmarks, meta, and A/B hooks.
STAGE3_PROMPT_VERSION = PROMPT_VERSION


def stage1_compact_line(stage1: dict[str, Any] | None) -> str:
    """Semantic Stage1 line for VLM task payload (replaces raw numeric-only injection)."""
    return stage1_semantic_lines(stage1)


def build_stage3_fast_prompt(*, blur_eff: str | None, stage1_features: dict[str, Any] | None) -> str:
    return compose_stage3_fast_prompt(
        blur_eff=blur_eff,
        stage1_features=stage1_features,
        stage1_line_fn=stage1_semantic_lines,
    )


def build_stage3_prompt(
    *,
    blur_eff: str | None,
    stage1_features: dict[str, Any] | None,
    strict_retry: bool = False,
) -> str:
    return compose_stage3_full_prompt(
        blur_eff=blur_eff,
        stage1_features=stage1_features,
        stage1_line_fn=stage1_semantic_lines,
        strict_retry=strict_retry,
        include_exemplar=not strict_retry,
    )
