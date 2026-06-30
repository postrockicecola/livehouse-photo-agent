"""Stage4 editing recommendations (optional follow-up VLM pass).

Stage3 intentionally omits ``editing_suggestions`` to reduce latency and scoring noise.
Wire this builder when a dedicated editing stage is enabled in the pipeline.
"""
from __future__ import annotations

STAGE4_PROMPT_VERSION = "stage4_editing_v1"
STAGE4_NUMERIC_PROMPT_VERSION = "stage4_numeric_v1"

# ~180 tokens — kept separate from Stage3 scoring contract.
STAGE4_EDITING_PROMPT_TEMPLATE = (
    "You suggest post-processing for one livehouse concert photo already scored.\n"
    "Return valid JSON only: one object with key editing_suggestions (array of 2-3 items).\n"
    "Each item: {\"zh\":\"...\",\"en\":\"...\"} with concrete Lightroom and/or Photoshop steps "
    "(exposure deltas, HSL, local masks, crop ratio + subject anchor).\n"
    "Avoid vague advice without parameters.\n"
    'Example: {"editing_suggestions":[{"zh":"Lightroom 曝光 +0.3EV，高光 -35","en":"Lightroom Exposure +0.3 EV, Highlights -35"},'
    '{"zh":"裁切 4:5，歌手面部居中","en":"Crop 4:5 anchoring the vocalist face"}]}\n'
    "Context scores: {dimension_summary}\n"
    "Strongest: {strongest_en}\n"
    "Weakest: {weakest_en}\n"
)


def build_stage4_editing_prompt(
    *,
    dimension_summary: str,
    strongest_en: str,
    weakest_en: str,
) -> str:
    # NOTE: use .replace (not .format) — the template embeds literal JSON braces
    # in its examples, which str.format would parse as fields (KeyError: '"zh"').
    return (
        STAGE4_EDITING_PROMPT_TEMPLATE
        .replace("{dimension_summary}", dimension_summary.strip() or "n/a")
        .replace("{strongest_en}", strongest_en.strip() or "n/a")
        .replace("{weakest_en}", weakest_en.strip() or "n/a")
    )


# Numeric grade contract: machine-appliable deltas (no human in the loop).
STAGE4_NUMERIC_PROMPT_TEMPLATE = (
    "You are an automatic photo retoucher for one livehouse concert photo.\n"
    "Return valid JSON only: one object with key \"adjustments\".\n"
    "adjustments fields (omit or 0 if no change):\n"
    "  exposure: EV stops, range -2.0..2.0\n"
    "  contrast, highlights, shadows, whites, blacks: range -100..100\n"
    "  temp (+warm/-cool), tint (+magenta/-green): range -100..100\n"
    "  vibrance, saturation, clarity: range -100..100\n"
    "Concert frames are usually under-exposed with harsh stage spotlights: lift exposure/shadows, "
    "tame highlights, keep skin natural. Be conservative; do not crush or blow out.\n"
    'Example: {"adjustments":{"exposure":0.6,"contrast":10,"highlights":-30,"shadows":22,'
    '"whites":-6,"blacks":-4,"temp":-5,"tint":2,"vibrance":15,"saturation":-3,"clarity":8}}\n'
    "Context scores: {dimension_summary}\n"
    "Strongest: {strongest_en}\n"
    "Weakest: {weakest_en}\n"
)


def build_stage4_numeric_prompt(
    *,
    dimension_summary: str,
    strongest_en: str,
    weakest_en: str,
) -> str:
    # NOTE: use .replace (not .format) — the template embeds a literal JSON example
    # ({"adjustments":{...}}) that str.format would parse as fields (KeyError: '"adjustments"').
    return (
        STAGE4_NUMERIC_PROMPT_TEMPLATE
        .replace("{dimension_summary}", dimension_summary.strip() or "n/a")
        .replace("{strongest_en}", strongest_en.strip() or "n/a")
        .replace("{weakest_en}", weakest_en.strip() or "n/a")
    )
