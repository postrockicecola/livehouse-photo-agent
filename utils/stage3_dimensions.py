"""Stage 3 VLM rubric: 8 dimensions (technical / visual / emotional / selection)."""

from __future__ import annotations

# Ordered keys used in prompts, parsing, logging, and UI.
STAGE3_DIM_KEYS: tuple[str, ...] = (
    "focus_sharpness",  # 对焦、解析力；允许有意图的运动模糊
    "exposure_control",  # 曝光、高光/阴影信息、舞台光比
    "noise_cleanliness",  # 噪点、涂抹、伪影、干净度
    "composition_framing",  # 景别、地平线、主次、裁切
    "light_color_character",  # 光影造型、色彩性格（非单纯亮暗）
    "moment_peak",  # 决定性瞬间、动作/表情峰值
    "atmosphere_impact",  # 现场能量、情绪与叙事感染力
    "deliverable_subject",  # 交付可用：表情、姿态、遮挡、主体完整性
)

STAGE3_DIM_ORDER: list[str] = list(STAGE3_DIM_KEYS)

STAGE3_DIM_LABELS: dict[str, str] = {
    "focus_sharpness": "对焦与清晰度",
    "exposure_control": "曝光与光比",
    "noise_cleanliness": "噪点与干净度",
    "composition_framing": "构图与取景",
    "light_color_character": "光影与色彩",
    "moment_peak": "瞬间与张力",
    "atmosphere_impact": "氛围与感染力",
    "deliverable_subject": "主体可用性",
}

# VLM prompt one-liners (English); keys must match STAGE3_DIM_KEYS order for maintainability.
STAGE3_DIM_PROMPT_LINES: dict[str, str] = {
    "focus_sharpness": "acutance/readability; intentional motion blur can score fairly if readable.",
    "exposure_control": "exposure, highlight/shadow detail, harsh stage contrast vs crush/clipping.",
    "noise_cleanliness": "noise, banding, artifacts, mushy denoise; high ISO ok if structure remains.",
    "composition_framing": "framing, horizon, balance, subject placement, accidental crops.",
    "light_color_character": "light sculpting + color mood (not exposure correctness alone).",
    "moment_peak": "decisive instant, gesture/expression peak, timing strength.",
    "atmosphere_impact": "crowd/stage energy, emotional storytelling, vibe.",
    "deliverable_subject": "client-ready: face usable, pose completeness, blocking/occlusion.",
}

# Strict JSON contract (single object) for Stage3 VLM — keys must stay aligned with parsers.
STAGE3_JSON_SCHEMA_LINES: tuple[str, ...] = (
    '{',
    '  "focus_sharpness": <number 0-10>,',
    '  "exposure_control": <number 0-10>,',
    '  "noise_cleanliness": <number 0-10>,',
    '  "composition_framing": <number 0-10>,',
    '  "light_color_character": <number 0-10>,',
    '  "moment_peak": <number 0-10>,',
    '  "atmosphere_impact": <number 0-10>,',
    '  "deliverable_subject": <number 0-10>,',
    '  "strongest_aspect": {"zh": "<string>", "en": "<string>"},',
    '  "weakest_aspect": {"zh": "<string>", "en": "<string>"},',
    '  "tags": ["<string>", "..."],',
    "}",
)
STAGE3_JSON_SCHEMA_TEXT = "\n".join(STAGE3_JSON_SCHEMA_LINES)
