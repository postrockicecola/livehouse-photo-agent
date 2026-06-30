"""Registry-driven Stage3 prompt fragments (versioned, composable layers)."""
from __future__ import annotations

from utils.stage3_dimensions import STAGE3_DIM_KEYS, STAGE3_DIM_PROMPT_LINES

PROMPT_VERSION = "stage3_v5"

# Token estimates (~4 chars/token, rough) for prompt budgeting.
PROMPT_BLOCK_TOKEN_HINTS: dict[str, int] = {
    "domain": 95,
    "contract": 85,
    "scoring_behavior": 55,
    "tags_behavior": 45,
    "exemplar": 120,
    "rubric": 110,
    "retry": 35,
}

PROMPT_BLOCKS: dict[str, str] = {
    # ~95 tokens — positive domain anchor + concert semantics prior
    "domain": (
        "You are scoring a livehouse / club / concert performance photograph.\n"
        "Treat the scene as on-stage performance energy: performer, lighting, crowd, and moment.\n"
        "If uncertain, bias toward concert and live-music semantics rather than generic outdoor or street scenes.\n"
        "Prefer tags and aspects that describe this specific frame, not generic stock labels.\n"
    ),
    # Tier A — hard requirements
    "contract_tier_a": (
        "Output rules (required):\n"
        "- Respond with valid JSON only: one top-level object.\n"
        "- No markdown fences, no prose outside the JSON object.\n"
    ),
    # Tier B — structure
    "contract_tier_b": (
        "Structure:\n"
        "- Include all eight dimension scores as numbers from 0 to 10 (decimals allowed).\n"
        '- "strongest_aspect" and "weakest_aspect": objects with "zh" and "en" strings.\n'
        '- "tags": JSON array of short strings (3–6 items).\n'
        "- Do not include editing_suggestions in this task.\n"
    ),
    # Tier C — soft behavior
    "contract_tier_c": (
        "Style:\n"
        "- Keep bilingual aspect lines concise and specific to this photo.\n"
        "- Prefer actionable wording over generic praise.\n"
        "- Avoid repeating the same idea across tags and aspects.\n"
    ),
    # ~55 tokens
    "scoring_behavior": (
        "Scoring guide: 4–5 acceptable, 6–7 good, 8–9 strong, 10 rare.\n"
        "moment_peak and atmosphere_impact matter for live music; intentional motion blur can still score well.\n"
        "Avoid score compression: differentiate average vs exceptional frames; reserve 8+ for standout moments.\n"
    ),
    # ~45 tokens
    "tags_behavior": (
        "Tags: prefer visually distinctive, scene-specific phrases (e.g. backlight haze, silhouette, "
        "peak motion, audience interaction, emotional tension, expressive gel lighting).\n"
        "Avoid repeating generic triplets like performer / stage lighting / crowd unless nothing else fits.\n"
    ),
}

STAGE3_COMPACT_EXEMPLAR = (
    '{"focus_sharpness":6.2,"exposure_control":5.8,"noise_cleanliness":7.1,'
    '"composition_framing":7.4,"light_color_character":8.0,"moment_peak":8.6,'
    '"atmosphere_impact":8.2,"deliverable_subject":6.9,'
    '"strongest_aspect":{"zh":"红 gel 侧光勾出歌手轮廓","en":"Red gel sidelight sculpts the vocalist silhouette"},'
    '"weakest_aspect":{"zh":"面部高光略过曝","en":"Facial highlights run slightly hot"},'
    '"tags":["backlight haze","peak motion","crowd silhouettes","expressive gel lighting"]}'
)

PROMPT_BLOCKS["retry"] = (
    "Previous output was invalid JSON.\n"
    "Re-emit corrected JSON only: a single minified object matching the required keys.\n"
)


def _dimension_rubric_block() -> str:
    lines = [f"{i}) {key}: {STAGE3_DIM_PROMPT_LINES[key]}" for i, key in enumerate(STAGE3_DIM_KEYS, 1)]
    return "Dimensions:\n" + "\n".join(lines) + "\n"


def build_system_core(*, include_exemplar: bool = True) -> str:
    """Static SYSTEM_CORE: domain + contract tiers + scoring/tags policy + optional exemplar."""
    parts = [
        PROMPT_BLOCKS["domain"],
        PROMPT_BLOCKS["contract_tier_a"],
        PROMPT_BLOCKS["contract_tier_b"],
        PROMPT_BLOCKS["contract_tier_c"],
        PROMPT_BLOCKS["scoring_behavior"],
        PROMPT_BLOCKS["tags_behavior"],
    ]
    if include_exemplar:
        parts.append(
            "Example output (shape and tone only; scores must match the image you see):\n"
            f"{STAGE3_COMPACT_EXEMPLAR}\n"
        )
    return "\n".join(parts)


def build_task_payload(
    *,
    blur_eff: str | None,
    stage1_features: dict | None,
    stage1_line_fn,
) -> str:
    """Dynamic TASK_PAYLOAD: signals, blur note, dimensional rubric."""
    stage1_line = stage1_line_fn(stage1_features)
    blur_note = ""
    if blur_eff == "artistic_motion_blur":
        blur_note = "Note: artistic motion blur is acceptable when the moment reads clearly.\n"
    elif blur_eff in ("motion_blur", "slight_blur"):
        blur_note = "Note: possible motion blur — balance intent vs readability.\n"

    return (
        f"{stage1_line}"
        f"{blur_note}"
        f"{_dimension_rubric_block()}"
    )


def build_retry_suffix() -> str:
    return PROMPT_BLOCKS["retry"]


def compose_stage3_full_prompt(
    *,
    blur_eff: str | None,
    stage1_features: dict | None,
    stage1_line_fn,
    strict_retry: bool = False,
    include_exemplar: bool = True,
) -> str:
    system = build_system_core(include_exemplar=include_exemplar and not strict_retry)
    task = build_task_payload(
        blur_eff=blur_eff,
        stage1_features=stage1_features,
        stage1_line_fn=stage1_line_fn,
    )
    suffix = build_retry_suffix() if strict_retry else ""
    return f"{system}\n{task}{suffix}"


def compose_stage3_fast_prompt(
    *,
    blur_eff: str | None,
    stage1_features: dict | None,
    stage1_line_fn,
) -> str:
    """Fast-first pass: compact JSON score + verdict + tags."""
    stage1_line = stage1_line_fn(stage1_features)
    blur_note = ""
    if blur_eff == "artistic_motion_blur":
        blur_note = "Note: artistic motion blur is acceptable if the moment reads.\n"
    elif blur_eff in ("motion_blur", "slight_blur"):
        blur_note = "Note: possible motion blur.\n"

    exemplar = (
        '{"score":82,"verdict":"Strong peak moment; highlights slightly hot.",'
        '"tags":["backlight haze","peak motion","expressive stage lighting"]}'
    )
    return (
        f"{PROMPT_BLOCKS['domain']}"
        f"{PROMPT_BLOCKS['contract_tier_a']}"
        "Structure: keys score (integer 0-100), verdict (one short English line), "
        "tags (array of 3-5 scene-specific strings).\n"
        f"Example:\n{exemplar}\n"
        f"{stage1_line}"
        f"{blur_note}"
    )
