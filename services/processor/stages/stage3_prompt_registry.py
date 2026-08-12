"""Registry-driven Stage3 prompt fragments (versioned, composable layers)."""
from __future__ import annotations

from utils.stage3_dimensions import STAGE3_DIM_KEYS, STAGE3_DIM_PROMPT_LINES

PROMPT_VERSION = "stage3_v9_route_b"

# Token estimates (~4 chars/token, rough) for prompt budgeting.
PROMPT_BLOCK_TOKEN_HINTS: dict[str, int] = {
    "domain": 95,
    "contract": 95,
    "scoring_behavior": 130,
    "tags_behavior": 70,
    "exemplar": 150,
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
        '- "dimensions": object containing all eight dimension scores as numbers '
        "from 0 to 10 (decimals allowed).\n"
        '- "strongest_aspect" and "weakest_aspect": objects with "zh" and "en" strings.\n'
        '- "tags": JSON array of short scene/object strings (3–6 items).\n'
        '- "mood_tags": JSON array of 1–3 short atmosphere/emotion labels '
        "(Chinese and/or English), grounded in what is visible.\n"
        '- "semantic_gate": visible semantic-defect observation with keys is_present '
        "(boolean), types (array), severity (integer 0–3), confidence (0–1), and "
        "evidence (one concrete visible fact).\n"
        "- Do not include editing_suggestions in this task.\n"
    ),
    # Tier C — soft behavior
    "contract_tier_c": (
        "Style:\n"
        "- Keep bilingual aspect lines concise and specific to this photo.\n"
        "- Prefer actionable wording over generic praise.\n"
        "- Avoid repeating the same idea across tags, mood_tags, and aspects.\n"
    ),
    # ~130 tokens. v6 anchored only 4-10, which left the model no licence to go low:
    # across 236 eval frames not one scored below 50 overall, and the bottom human
    # quintile came back +21 points high. The bottom of the scale has to be named.
    "scoring_behavior": (
        "Scoring guide — the whole 0–10 range is in use:\n"
        "  0–2  unusable: no recovery path\n"
        "  3–4  weak: a visible defect a client would reject\n"
        "  5–6  usable: fine in a bulk gallery, not a highlight\n"
        "  7–8  strong: would ship as a selected frame\n"
        "  9–10 exceptional: rare, portfolio-grade\n"
        "A real shoot reaches the bottom of that scale: missed focus, the subject buried, "
        "nothing happening. When this frame is one of those, score it in the 1–4 band and "
        "let the total fall with it. Refusing to go below 5 is the worst failure here, "
        "because it makes the frames worth discarding indistinguishable from the keepers.\n"
        "Being generous is not being fair: a 7 handed out for free costs a genuinely "
        "strong frame the distance it earned. Reserve 8+ for standout moments.\n"
        "Do not give all eight dimensions the same value — a frame is normally strong on "
        "some axes and weak on others. Vary the decimal; do not emit only .0 and .5.\n"
        "moment_peak and atmosphere_impact matter for live music; intentional motion blur can still score well.\n"
    ),
    # ~70 tokens
    "tags_behavior": (
        "Tags: visually distinctive scene/object phrases "
        "(e.g. backlight haze, silhouette, peak motion, audience interaction, expressive gel lighting).\n"
        "Mood_tags: how the frame feels from visible cues — solitude on an empty stage, dense pit euphoria, "
        "cool melancholy gel, tense peak, calm afterglow. Prefer short labels such as "
        "孤独, 疏离, 宁静, 热烈, 忧郁, lonely, euphoric, tense, melancholic.\n"
        "Ground mood in composition/lighting/subject density; do not invent backstories.\n"
        "Avoid generic triplets like performer / stage lighting / crowd unless nothing else fits.\n"
    ),
    "semantic_gate_behavior": (
        "Semantic safety observation:\n"
        "- Allowed types only: closed_eyes, heavy_occlusion, no_clear_subject, "
        "missed_moment, severe_composition_failure, bad_expression, invalid_pose, other.\n"
        "- Mark is_present only for a visible client-delivery defect, not merely a quiet "
        "or unconventional frame. Intentional silhouettes and readable motion blur are not defects.\n"
        "- severity: 0 absent, 1 minor, 2 material, 3 clearly unusable. "
        "confidence is confidence in this present/absent observation, not aesthetic score confidence.\n"
        "- evidence must describe what is visibly wrong in this exact frame; use an empty "
        "string when no defect is present. Do not make the final reject decision.\n"
    ),
}

# v6 shipped a filled example -- concrete scores plus concrete Chinese aspect text --
# and the model copied it instead of reading it as shape. Measured over 236 eval
# frames: 60% reproduced at least 6 of the 8 numbers bit-identically, 78% returned the
# example's "面部高光略过曝" verbatim as their own weakness, and 41% echoed all four of
# its tags. Per-dimension adherence to the example correlated -0.85 with agreement
# against human labels, and the only dimension that escaped it (focus_sharpness, 22%
# adherence) was the only one with real signal (Spearman 0.45 vs ~0 for the rest).
# A placeholder schema gives up the tone hint; that hint was costing the whole score
# range. Keep every value a slot the model must fill from the image in front of it.
STAGE3_COMPACT_EXEMPLAR = (
    '{"dimensions":{"focus_sharpness":<0-10, one decimal>,'
    '"exposure_control":<0-10, one decimal>,'
    '"noise_cleanliness":<0-10, one decimal>,"composition_framing":<0-10, one decimal>,'
    '"light_color_character":<0-10, one decimal>,"moment_peak":<0-10, one decimal>,'
    '"atmosphere_impact":<0-10, one decimal>,"deliverable_subject":<0-10, one decimal>},'
    '"strongest_aspect":{"zh":"<这一张最强的地方，简短具体>","en":"<same, English>"},'
    '"weakest_aspect":{"zh":"<这一张最弱的地方，简短具体>","en":"<same, English>"},'
    '"tags":["<visible scene/object phrase>","..."],'
    '"mood_tags":["<atmosphere label>","..."],'
    '"semantic_gate":{"is_present":<true or false>,"types":["<allowed type or empty>"],'
    '"severity":<0-3>,"confidence":<0-1>,"evidence":"<visible fact or empty>"}}'
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
        PROMPT_BLOCKS["semantic_gate_behavior"],
    ]
    if include_exemplar:
        parts.append(
            "Emit exactly this shape. Every <...> is a slot you must fill by reading "
            "THIS image -- the placeholders are not example values, and echoing them "
            "back is a failed response:\n"
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

    # Same placeholder-only rule as the full pass: a filled example here showed a
    # concrete 82 and got echoed back as the model's own score.
    schema = (
        '{"score":<integer 0-100>,"verdict":"<one short English line about THIS frame>",'
        '"dimensions":{},'
        '"tags":["<visible scene/object phrase>","..."],'
        '"mood_tags":["<atmosphere label>","..."],'
        '"semantic_gate":{"is_present":<true or false>,"types":["<allowed type or empty>"],'
        '"severity":<0-3>,"confidence":<0-1>,"evidence":"<visible fact or empty>"}}'
    )
    return (
        f"{PROMPT_BLOCKS['domain']}"
        f"{PROMPT_BLOCKS['contract_tier_a']}"
        "Structure: keys score (integer 0-100), verdict (one short English line), "
        "dimensions (empty object in fast mode), "
        "tags (array of 3-5 scene/object strings), "
        "mood_tags (array of 1–3 atmosphere/emotion labels, Chinese and/or English), "
        "semantic_gate (visible semantic-defect observation).\n"
        "Score bands: 0–25 unusable, 26–45 weak, 46–65 usable, 66–85 strong, 86–100 rare. "
        "A real shoot produces frames in the bottom two bands; score them there rather "
        "than compressing everything into 70–85.\n"
        f"{PROMPT_BLOCKS['tags_behavior']}"
        f"{PROMPT_BLOCKS['semantic_gate_behavior']}"
        f"Emit exactly this shape, filling every <...> from the image:\n{schema}\n"
        f"{stage1_line}"
        f"{blur_note}"
    )


def compose_stage3_semantic_first_prompt() -> str:
    """A/B candidate: inspect reject evidence before producing the fast score."""
    schema = (
        '{"semantic_gate":{"is_present":<true or false>,'
        '"types":["<allowed type or empty>"],"severity":<0-3>,'
        '"confidence":<0-1>,"evidence":"<visible fact or empty>"},'
        '"score":<integer 0-100>,"verdict":"<one short line>",'
        '"tags":["<visible phrase>","..."],"mood_tags":["<atmosphere>","..."]}'
    )
    return (
        f"{PROMPT_BLOCKS['domain']}"
        f"{PROMPT_BLOCKS['contract_tier_a']}"
        "Your first and most important task is delivery-safety inspection. "
        "Do not score the image until that inspection is complete.\n"
        "Inspect each risk independently: clearly closed eyes; failed expression; "
        "face/body hidden by foreground objects; no identifiable visual subject; "
        "a between-actions non-moment; destructive crop/tilt/imbalance; invalid pose.\n"
        "This batch intentionally contains both rejects and good frames. Do not use "
        "false as a safe default, and do not let attractive stage light excuse a "
        "delivery-blocking defect. Equally, do not reject intentional silhouette or "
        "readable expressive motion merely because a face is not frontal.\n"
        f"{PROMPT_BLOCKS['semantic_gate_behavior']}"
        "After the safety observation, assign the fast aesthetic score independently. "
        "A semantic reject may still look atmospheric and receive a moderate score.\n"
        f"{PROMPT_BLOCKS['tags_behavior']}"
        f"Emit exactly this shape, replacing every placeholder from THIS image:\n{schema}\n"
    )


def compose_stage3_semantic_compact_prompt() -> str:
    """A/B candidate for small local VLMs: compact Chinese gate-first contract."""
    return (
        "你是 Livehouse 演出摄影质检员。只完成两个独立任务，并且只输出一个 JSON 对象。\n"
        "任务1（先做）：检查是否有影响交付的明确语义废片问题。逐项检查：闭眼或表情崩坏、"
        "前景重度遮挡主体、没有可识别的表演主体、错过动作/情绪瞬间、破坏性的裁切倾斜失衡、"
        "无效姿态。批次中确实有废片，不要把 false 当默认答案；但可读的剪影、表现性动态模糊"
        "和非正面脸本身不算缺陷。severity：0无、1轻微、2明显影响交付、3确定废片。"
        "confidence 表示对存在/不存在判断的把握。\n"
        "任务2（后做）：独立给出 0-100 美学分；有氛围但存在语义缺陷的图仍可有中等分。\n"
        "types 只允许：closed_eyes, heavy_occlusion, no_clear_subject, missed_moment, "
        "severe_composition_failure, bad_expression, invalid_pose, other。"
        "没有缺陷时 types=[]、severity=0、evidence=\"\"。\n"
        "JSON 格式："
        '{"semantic_gate":{"is_present":<true或false>,"types":[<缺陷类型或留空>],'
        '"severity":<0到3>,"confidence":<0到1>,"evidence":"<可见事实或留空>"},'
        '"score":<0到100>,"verdict":"<一句话>","tags":["<可见事实>"],'
        '"mood_tags":["<氛围词>"]}'
    )
