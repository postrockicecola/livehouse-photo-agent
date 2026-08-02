"""Map natural-language session vibe prompts to closed-set ``film_*`` variants."""
from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass
from typing import Any

from services.film_render_service import FILM_VARIANT_IDS

# Sentinel for export/API when resolving from persisted session (not passed to op_kernel).
FILM_VARIANT_SESSION_VIBE = "session_vibe"

_DEFAULT_VARIANT = "film_livehouse"
_UNMATCHED_MATCHED_BY = frozenset({"rules:fallback", "rules:default", "llm:failed"})


@dataclass(frozen=True)
class FilmVibeDecision:
    film_variant: str
    label_zh: str
    reason_zh: str
    matched_by: str  # e.g. "rules:romantic_retro", "llm:ollama"
    prompt: str = ""
    intensity: float = 1.0
    matched: bool = False

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        if self.film_variant not in FILM_VARIANT_IDS:
            d["film_variant"] = _DEFAULT_VARIANT
        d["matched"] = bool(self.matched)
        return d


# (score_weight, variant_id, label_zh, reason_zh, tag) — keywords matched case-insensitively.
_VIBE_RULES: tuple[tuple[int, str, str, str, str], ...] = (
    (11, "film_mexico_sun", "Mexico Sun", "墨西哥/炎热/金黄/沙漠气息", "mexico_sun"),
    (11, "film_spain_passion", "Spain Passion", "西班牙/热情/红色", "spain_passion"),
    (10, "film_latin_cinema", "Latin Cinema", "老电影/拉美/复古电影感", "latin_cinema"),
    (11, "film_wong_kar_wai", "王家卫", "王家卫/港片/花样年华/重庆森林/暧昧情绪", "wong_kar_wai"),
    (10, "film_retro_literary_portrait", "复古文艺人像", "文艺/人像/复古写真/胶片人像/情绪人像", "literary_portrait"),
    (12, "film_cold_v2", "暖色 · 浪漫复古", "浪漫/复古/暖调关键词", "romantic_retro"),
    (10, "film_cold_v4", "电影 · 影院", "电影感/影院关键词", "cinema"),
    (12, "film_hp5_bw", "Ilford HP5 · 黑白纪实", "黑白/纪实/HP5 关键词", "bw_doc"),
    (11, "film_tri_x_bw", "Kodak Tri-X · 黑白", "Tri-X/黑白胶片关键词", "bw_trix"),
    (10, "film_ricoh_gr", "理光 · 正片街拍", "街拍/理光/纪实关键词", "ricoh_street"),
    (9, "film_black_mist", "黑柔 · 柔光", "柔和/黑柔/雾感关键词", "black_mist"),
    (9, "film_cold_v3", "冷调 · 清冷", "冷色/青调关键词", "cool"),
    (9, "film_cinestill_800t", "Cinestill 800T", "夜景/霓虹/800T 关键词", "cinestill"),
    (8, "film_portra_400", "Portra 400", "人像/婚礼/肤色关键词", "portra"),
    (8, "film_fuji_classic_neg", "Fuji Classic Neg", "日系/静物/经典负片关键词", "fuji_cn"),
    (7, "film_gold_200", "Gold 200", "旅行/快照/暖黄关键词", "gold"),
    (10, "film_neon_club", "Neon Club", "俱乐部/激光/紫绿霓虹", "neon_club"),
    (10, "film_neon_tokyo", "Neon Tokyo", "东京夜景/灯牌霓虹", "neon_tokyo"),
    (10, "film_neon_pop", "Neon Pop", "霓虹/pop/演唱会灯光", "neon_pop"),
    (9, "film_neon_magenta", "Neon Magenta", "品红/粉紫霓虹", "neon_magenta"),
    (9, "film_neon_cyan", "Neon Cyan", "青色电光霓虹", "neon_cyan"),
    (8, "film_neon_signage", "Neon Signage", "招牌/红蓝霓虹", "neon_signage"),
    (8, "film_neon_haze", "Neon Haze", "雾光/朦胧霓虹", "neon_haze"),
    (9, "film_ultra_vivid", "Ultra Vivid", "绚丽/霓虹/高饱和关键词", "ultra_vivid"),
    (8, "film_teal_magenta", "Teal & Magenta", "青橙/电影感绚丽", "teal_magenta"),
    (8, "film_sunset_chrome", "Sunset Chrome", "日落/金色/暖艳", "sunset"),
    (8, "film_velvia_50", "Velvia 50", "风景/Velvia/翠绿关键词", "velvia"),
    (8, "film_lomo_xpro", "Lomo X-Pro", "LOMO/交叉冲洗/夸张色彩", "lomo"),
    (7, "film_kodachrome_64", "Kodachrome 64", "柯达/chrome/经典鲜艳", "kodachrome"),
    (7, "film_ektar_100", "Ektar 100", "风景/饱和/鲜艳关键词", "ektar"),
    (7, "film_superia_400", "Superia 400", "日系快照/鲜艳/消费卷", "superia"),
    (7, "film_cinestill_50d", "Cinestill 50D", "日光/电影/50D 关键词", "cinestill50"),
    (7, "film_livehouse", "Livehouse 现场", "现场/演出/livehouse 关键词", "livehouse"),
)

_KEYWORDS: dict[str, tuple[str, ...]] = {
    "mexico_sun": (
        "墨西哥",
        "mexico",
        "oaxaca",
        "炎热",
        "沙漠",
        "阳光",
        "金黄",
        "latino",
        "latin",
        "heat",
        "sun baked",
    ),
    "spain_passion": (
        "西班牙",
        "spain",
        "马德里",
        "热情",
        "红色",
        "flamenco",
        "almodovar",
        "iberia",
    ),
    "latin_cinema": (
        "老电影",
        "拉美电影",
        "latin cinema",
        "35mm",
        "电影感",
        "复古电影",
        "cinema retro",
    ),
    "wong_kar_wai": (
        "王家卫",
        "wong kar-wai",
        "wong kar wai",
        "花样年华",
        "重庆森林",
        "春光乍泄",
        "堕落天使",
        "港片",
        "香港",
        "暧昧",
        "情绪",
        "1990s hk",
    ),
    "literary_portrait": (
        "文艺",
        "文艺人像",
        "复古人像",
        "人像写真",
        "情绪人像",
        "胶片人像",
        "literary",
        "portrait mood",
        "soft portrait",
        "复古写真",
    ),
    "romantic_retro": (
        "浪漫",
        "复古",
        "怀旧",
        "暖",
        "暖色",
        "黄昏",
        "夕阳",
        "胶片感",
        "复古胶片",
        "romantic",
        "retro",
        "vintage",
        "warm",
        "nostalg",
    ),
    "cinema": ("电影", "影院", "戏剧", "大片", "cinema", "cinematic", "theatrical", "movie"),
    "bw_doc": (
        "黑白",
        "黑白纪实",
        "纪实黑白",
        "b&w",
        "b/w",
        "bw",
        "monochrome",
        "hp5",
        "ilford",
        "documentary bw",
        "黑白的",
        "出一组黑白",
    ),
    "bw_trix": (
        "tri-x",
        "tri x",
        "trix",
        "kodak bw",
        "柯达黑白",
    ),
    "ricoh_street": (
        "理光",
        "ricoh",
        "gr3",
        "gr ",
        "街拍",
        "纪实",
        "snapshot",
        "street",
        "documentary",
        "正片",
    ),
    "black_mist": ("黑柔", "柔光", "朦胧", "mist", "soft", "dreamy", "柔", "雾"),
    "cool": ("冷", "冷调", "清冷", "青", "蓝", "cool", "cold", "teal", "cyan"),
    "cinestill": ("cinestill", "800t", "夜景", "霓虹", "neon", "night", "钨丝", "tungsten"),
    "cinestill50": ("50d", "日光", "白天", "daylight", "cinestill 50"),
    "portra": ("portra", "人像", "婚礼", "写真", "肤色", "portrait", "wedding", "skin"),
    "fuji_cn": ("classic neg", "经典负片", "日系", "富士", "fuji", "provia", "nc ", "cn "),
    "gold": ("gold 200", "柯达金", "快照", "旅行", "snapshot", "vacation", "gold"),
    "ektar": ("ektar", "风景", "饱和", "鲜艳", "landscape", "vivid", "saturation"),
    "ultra_vivid": ("绚丽", "鲜艳", "高饱和", "霓虹", "neon", "pop", "vivid", "saturated", "色彩"),
    "velvia": ("velvia", "反转", "风景", "翠绿", "sky", "landscape slide"),
    "lomo": ("lomo", "交叉", "xpro", "x-pro", "实验", "夸张"),
    "kodachrome": ("kodachrome", "柯达", "64", "chrome", "经典幻灯"),
    "superia": ("superia", "快照", "鲜艳", "400", "fuji 400"),
    "neon_pop": ("neon", "霓虹", "灯", "紫", "蓝", "舞台灯", "concert light"),
    "neon_tokyo": ("neon tokyo", "东京", "夜景", "灯牌", "signage", "cyber", "街道"),
    "neon_cyan": ("cyan", "青色", "电光", "蓝绿", "ice neon"),
    "neon_magenta": ("magenta", "品红", "粉紫", "洋红", "pink neon"),
    "neon_club": ("club", "俱乐部", "激光", "紫绿", "舞池", "live set"),
    "neon_signage": ("signage", "招牌", "广告牌", "red blue neon"),
    "neon_haze": ("haze", "雾", "朦胧", "发光", "glow", "diffused"),
    "teal_magenta": ("青橙", "teal", "magenta", "橙青", "电影感"),
    "sunset": ("日落", "黄昏", "金色", "sunset", "golden hour", "暖艳"),
    "livehouse": ("livehouse", "现场", "演出", "舞台", "演唱会", "concert", "gig", "音乐节"),
}


def _normalize_prompt(prompt: str) -> str:
    t = (prompt or "").strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


# Relative “make it stronger” asks (not a new named style).
_RELATIVE_INTENSITY_RE = re.compile(
    r"(?:"
    r"(?:颜色|色彩|饱和度?).{0,6}(?:再|更).{0,4}(?:浓|重|艳|饱和|狠|强)|"
    r"(?:再|更)(?:浓烈|浓郁|浓|重|狠|强|艳).{0,4}(?:一些|一点|点|些)?|"
    r"(?:胶片感|风格).{0,6}(?:更狠|更重|更强|再浓|更浓)|"
    r"(?:颜色|饱和度?).{0,4}拉满|拉满(?:颜色|饱和)|"
    r"(?:超狠|极致|狠狠|非常非常)"
    r")",
    re.IGNORECASE,
)
_STYLE_SWITCH_RE = re.compile(r"(换成|改成|修成|套上|套成|应用)")


def is_relative_intensity_ask(prompt: str) -> bool:
    """True for「颜色再浓烈一些」/「胶片感更狠」without an explicit style switch verb."""
    t = (prompt or "").strip()
    if not t or _RELATIVE_INTENSITY_RE.search(t) is None:
        return False
    if _STYLE_SWITCH_RE.search(t) is not None:
        return False
    return True


def _intensity_from_prompt(prompt: str) -> float:
    """Boost grade strength for 更狠 / 再浓烈 / 非常复古 style asks."""
    t = _normalize_prompt(prompt)
    if not t:
        return 1.0
    if any(k in t for k in ("非常非常", "超狠", "极致", "拉满", "狠狠")):
        return 1.35
    if any(
        k in t
        for k in (
            "更狠",
            "更重",
            "更强",
            "狠一些",
            "重一些",
            "强一些",
            "非常复古",
            "浓烈",
            "浓郁",
            "再浓",
            "更浓",
            "再浓烈",
            "更浓烈",
            "颜色更",
            "更艳",
            "更饱和",
            "饱和度更高",
            "浓一点",
            "再重",
        )
    ):
        return 1.2
    return 1.0


def _with_intensity(decision: FilmVibeDecision) -> FilmVibeDecision:
    intensity = _intensity_from_prompt(decision.prompt)
    if abs(intensity - decision.intensity) < 1e-6:
        return decision
    return FilmVibeDecision(
        film_variant=decision.film_variant,
        label_zh=decision.label_zh,
        reason_zh=decision.reason_zh,
        matched_by=decision.matched_by,
        prompt=decision.prompt,
        intensity=intensity,
        matched=decision.matched,
    )


def _strip_intensity_phrases(prompt: str) -> str:
    """Remove relative-intensity wording so leftover style tokens can be checked."""
    t = _normalize_prompt(prompt)
    t = _RELATIVE_INTENSITY_RE.sub(" ", t)
    for tok in (
        "胶片感",
        "颜色",
        "色彩",
        "饱和度",
        "饱和",
        "风格",
        "一些",
        "一点",
        "点",
        "些",
        "再",
        "更",
        "更加",
        "需要",
        "的",
        " ",
    ):
        t = t.replace(tok, " ")
    return re.sub(r"\s+", "", t)


def _is_intensity_only_prompt(prompt: str, rules_dec: FilmVibeDecision) -> bool:
    if not is_relative_intensity_ask(prompt):
        return False
    if not rules_dec.matched or rules_dec.matched_by in _UNMATCHED_MATCHED_BY:
        return True
    return len(_strip_intensity_phrases(prompt)) <= 1


def _clamp_intensity(value: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 1.0
    if v != v:  # NaN
        return 1.0
    return round(max(0.0, min(1.5, v)), 3)


def _boost_prior_session(prior: dict[str, Any], prompt: str) -> FilmVibeDecision:
    """Keep current film_variant; step intensity up for relative「再浓一点」asks."""
    prior_i = _clamp_intensity(prior.get("intensity") or 1.0)
    asked = _intensity_from_prompt(prompt)
    if asked <= 1.0 + 1e-6:
        asked = 1.2
    new_i = _clamp_intensity(max(asked, prior_i + 0.15))
    variant = str(prior.get("film_variant") or _DEFAULT_VARIANT).strip()
    if variant not in FILM_VARIANT_IDS:
        variant = _DEFAULT_VARIANT
    label = str(prior.get("label_zh") or variant).strip() or variant
    return FilmVibeDecision(
        film_variant=variant,
        label_zh=label,
        reason_zh=f"在当前风格上加强浓度（强度 {new_i:.2f}）",
        matched_by="rules:intensity_boost",
        prompt=(prompt or "").strip(),
        intensity=new_i,
        matched=True,
    )


def _resolve_vibe_from_rules(prompt: str) -> FilmVibeDecision:
    raw = (prompt or "").strip()
    norm = _normalize_prompt(raw)
    if not norm:
        return FilmVibeDecision(
            film_variant=_DEFAULT_VARIANT,
            label_zh="默认 · Livehouse",
            reason_zh="未输入描述，使用默认现场风格",
            matched_by="rules:default",
            prompt=raw,
            matched=False,
        )

    best_score = 0
    best: tuple[str, str, str, str] | None = None
    for weight, variant_id, label_zh, reason_tpl, tag in _VIBE_RULES:
        if variant_id not in FILM_VARIANT_IDS:
            continue
        kws = _KEYWORDS.get(tag, ())
        hits = sum(1 for kw in kws if kw in norm)
        if hits <= 0:
            continue
        score = weight * hits
        if score > best_score:
            best_score = score
            best = (variant_id, label_zh, f"{reason_tpl}（命中 {hits} 个词）", f"rules:{tag}")

    if best is None:
        return FilmVibeDecision(
            film_variant=_DEFAULT_VARIANT,
            label_zh="默认 · Livehouse",
            reason_zh=f"未识别关键词「{raw[:40]}」",
            matched_by="rules:fallback",
            prompt=raw,
            matched=False,
        )

    variant_id, label_zh, reason_zh, matched_by = best
    return FilmVibeDecision(
        film_variant=variant_id,
        label_zh=label_zh,
        reason_zh=reason_zh,
        matched_by=matched_by,
        prompt=raw,
        matched=True,
    )


def resolve_vibe_from_prompt(
    prompt: str,
    *,
    prior_session: dict[str, Any] | None = None,
) -> FilmVibeDecision:
    """Keyword rules first; on ``rules:fallback`` only, optional Ollama text → JSON.

    When ``prior_session`` is a matched vibe and the prompt is a relative intensity
    ask (e.g. 颜色再浓烈一些), keep the current ``film_variant`` and only raise
    ``intensity``.
    """
    rules_dec = _resolve_vibe_from_rules(prompt)
    prior = prior_session if session_vibe_is_matched(prior_session) else None
    if is_relative_intensity_ask(prompt) and _is_intensity_only_prompt(prompt, rules_dec):
        if prior is not None:
            return _boost_prior_session(prior, prompt)
        return FilmVibeDecision(
            film_variant=_DEFAULT_VARIANT,
            label_zh="默认 · Livehouse",
            reason_zh="请先选定一种胶片风格，再说「颜色再浓烈一些」",
            matched_by="rules:intensity_needs_prior",
            prompt=(prompt or "").strip(),
            matched=False,
        )
    if prior is not None and is_relative_intensity_ask(prompt):
        same_variant = str(prior.get("film_variant") or "") == rules_dec.film_variant
        if same_variant:
            return _boost_prior_session(prior, prompt)

    if rules_dec.matched_by != "rules:fallback":
        return _with_intensity(rules_dec)

    from services.vibe_llm_resolver import try_resolve_vibe_via_llm

    llm_dec = try_resolve_vibe_via_llm(rules_dec.prompt)
    if llm_dec is not None:
        return _with_intensity(llm_dec)

    return _with_intensity(
        FilmVibeDecision(
            film_variant=rules_dec.film_variant,
            label_zh=rules_dec.label_zh,
            reason_zh=f"{rules_dec.reason_zh}；AI 未能解析，未应用默认修图",
            matched_by="llm:failed",
            prompt=rules_dec.prompt,
            matched=False,
        )
    )


def session_vibe_is_matched(session_vibe: dict[str, Any] | None) -> bool:
    if not session_vibe:
        return False
    if "matched" in session_vibe:
        return bool(session_vibe.get("matched"))
    mb = str(session_vibe.get("matched_by") or "")
    return mb not in _UNMATCHED_MATCHED_BY


def effective_film_variant_for_export(
    *,
    spec_film_variant: str | None,
    session_vibe: dict[str, Any] | None,
    use_session_vibe: bool,
    default_variant: str = _DEFAULT_VARIANT,
) -> str | None:
    """Per-image export variant: explicit preset wins; else optional session vibe (must be matched)."""
    fv = (spec_film_variant or "").strip()
    if fv in FILM_VARIANT_IDS:
        return fv
    if fv == FILM_VARIANT_SESSION_VIBE:
        fv = ""
    if use_session_vibe and session_vibe and session_vibe_is_matched(session_vibe):
        sv = str(session_vibe.get("film_variant") or "").strip()
        if sv in FILM_VARIANT_IDS:
            return sv
    if fv:
        return None
    if use_session_vibe and session_vibe and session_vibe_is_matched(session_vibe):
        sv = str(session_vibe.get("film_variant") or "").strip()
        if sv in FILM_VARIANT_IDS:
            return sv
    return None


def session_vibe_payload_from_decision(decision: FilmVibeDecision) -> dict[str, Any]:
    now = int(time.time())
    return {
        "prompt": decision.prompt,
        "film_variant": decision.film_variant,
        "label_zh": decision.label_zh,
        "reason_zh": decision.reason_zh,
        "matched_by": decision.matched_by,
        "intensity": decision.intensity,
        "matched": bool(decision.matched),
        "updated_unix": now,
    }
