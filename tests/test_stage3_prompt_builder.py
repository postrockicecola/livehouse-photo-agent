"""Stage3 prompt registry and builder tests."""
from __future__ import annotations

from services.processor.stages.stage1_signal_semantics import stage1_semantic_lines
from services.processor.stages.stage3_output_validation import sanitize_stage3_parsed
from services.processor.stages.stage3_prompt_builder import (
    STAGE3_PROMPT_VERSION,
    build_stage3_prompt,
)
from services.processor.stages.stage3_prompt_registry import PROMPT_BLOCKS
from utils.stage3_dimensions import STAGE3_DIM_KEYS


def test_prompt_version_is_explicit() -> None:
    assert STAGE3_PROMPT_VERSION == "stage3_v5"


def test_full_prompt_uses_layered_contract_not_editing_rules() -> None:
    prompt = build_stage3_prompt(blur_eff=None, stage1_features=None, strict_retry=False)
    assert "editing_suggestions" not in prompt or "Do not include editing_suggestions" in prompt
    assert "NON-NEGOTIABLE" not in prompt
    assert "Example output" in prompt
    assert PROMPT_BLOCKS["domain"][:20] in prompt


def test_retry_prompt_is_calmer() -> None:
    prompt = build_stage3_prompt(blur_eff=None, stage1_features=None, strict_retry=True)
    assert "Previous output was invalid JSON" in prompt
    assert "NON-NEGOTIABLE" not in prompt


def test_stage1_semantic_line_not_raw_only() -> None:
    line = stage1_semantic_lines(
        {
            "laplacian_var": 42.0,
            "highlight_frac": 0.25,
            "blur_type": "motion_blur",
        }
    )
    assert "low sharpness" in line
    assert "laplacian_var=42" in line
    assert "blur_type=motion_blur" in line


def test_sanitize_clears_editing_and_dedupes_tags() -> None:
    dims = {k: 7.0 for k in STAGE3_DIM_KEYS}
    parsed = sanitize_stage3_parsed(
        {
            "dimensions": dims,
            "tags": ["Crowd", "crowd", "Backlight haze"],
            "editing_suggestions": [{"zh": "x", "en": "y"}],
            "strongest_aspect": {"zh": "a", "en": "b"},
            "weakest_aspect": {"zh": "c", "en": "d"},
        }
    )
    assert parsed["editing_suggestions"] == []
    assert parsed["tags"] == ["Crowd", "Backlight haze"]
