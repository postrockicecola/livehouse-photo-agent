"""Inference response parsing helpers for stage3 JSON output.

Parsing strategy (most-reliable first):
  1. Pydantic schema validation via ``inference.schemas`` — catches type errors and
     range violations declaratively; emits the legacy dict contract via ``to_parsed_dict``.
  2. Manual key extraction (existing logic) — handles VLM outputs where some optional
     fields are absent but the required dimension scores are present.
  3. Regex recovery — last resort for truncated / malformed JSON where ``json.loads`` fails.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from utils.stage3_dimensions import STAGE3_DIM_KEYS

logger = logging.getLogger(__name__)

_LOG_CLIP = 6000
_DEFAULT_FLOAT = 5.0


def norm_bilingual_text(v: Any) -> dict[str, str]:
    """Normalize model field to {zh, en}; legacy plain string duplicates to both."""
    if isinstance(v, dict):
        return {
            "zh": str(v.get("zh", "") or "").strip(),
            "en": str(v.get("en", "") or "").strip(),
        }
    s = str(v or "").strip()
    return {"zh": s, "en": s}


def _norm_edit_item(v: Any) -> Optional[dict[str, str]]:
    if isinstance(v, dict):
        zh = str(v.get("zh", "") or "").strip()
        en = str(v.get("en", "") or "").strip()
        if zh or en:
            return {"zh": zh, "en": en}
        return None
    s = str(v or "").strip()
    if s:
        return {"zh": s, "en": s}
    return None


def _regex_bilingual_field(json_str: str, key: str) -> dict[str, str]:
    """Last-resort extraction for strongest/weakest as bilingual object or legacy string."""
    m_obj_zh_first = re.search(
        rf'"{re.escape(key)}"\s*:\s*\{{\s*"zh"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"en"\s*:\s*"((?:[^"\\]|\\.)*)"',
        json_str,
        re.DOTALL,
    )
    if m_obj_zh_first:
        return {
            "zh": m_obj_zh_first.group(1).replace("\\n", " ").strip()[:500],
            "en": m_obj_zh_first.group(2).replace("\\n", " ").strip()[:500],
        }
    m_obj_en_first = re.search(
        rf'"{re.escape(key)}"\s*:\s*\{{\s*"en"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"zh"\s*:\s*"((?:[^"\\]|\\.)*)"',
        json_str,
        re.DOTALL,
    )
    if m_obj_en_first:
        return {
            "zh": m_obj_en_first.group(2).replace("\\n", " ").strip()[:500],
            "en": m_obj_en_first.group(1).replace("\\n", " ").strip()[:500],
        }
    m_str = re.search(rf'"{re.escape(key)}"\s*:\s*"((?:[^"\\]|\\.)*)"', json_str)
    if m_str:
        return norm_bilingual_text(m_str.group(1))
    return {"zh": "", "en": ""}


def _fallback_parse_truncated_json(json_str: str) -> Optional[dict[str, Any]]:
    """
    Last-resort recovery when ``json.loads`` fails (truncation or malformed tail).
    Requires all eight 0-10 dimension numbers to appear as literals in the text.
    """
    dimensions: dict[str, float] = {}
    for dim in STAGE3_DIM_KEYS:
        m = re.search(rf'"{re.escape(dim)}"\s*:\s*([0-9]+(?:\.[0-9]+)?)', json_str)
        if not m:
            return None
        try:
            dimensions[dim] = max(0.0, min(10.0, float(m.group(1))))
        except ValueError:
            dimensions[dim] = 5.0

    dimension_comments: dict[str, dict[str, str]] = {}
    for dim in STAGE3_DIM_KEYS:
        pat = rf'"{re.escape(dim)}"\s*:\s*"((?:[^"\\]|\\.)*)'
        m = re.search(pat, json_str)
        if m:
            val = m.group(1).replace("\\n", " ").strip()
            if val:
                v = val[:500]
                dimension_comments[dim] = {"zh": v, "en": v}

    strongest = _regex_bilingual_field(json_str, "strongest_aspect")
    weakest = _regex_bilingual_field(json_str, "weakest_aspect")

    tags: list = []
    tm = re.search(r'"tags"\s*:\s*\[([^\]]*)\]', json_str, re.DOTALL)
    if tm:
        for t in re.findall(r'"([^"]*)"', tm.group(1)):
            if t.strip():
                tags.append(t)

    editing_suggestions: list = []
    es_m = re.search(r'"editing_suggestions"\s*:\s*\[([^\]]*)\]', json_str, re.DOTALL)
    block = es_m.group(1) if es_m else ""
    if block.strip():
        pair_pat = r'\{\s*"zh"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"en"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
        for zm, em in re.findall(pair_pat, block):
            editing_suggestions.append({"zh": zm.strip()[:300], "en": em.strip()[:300]})
        if not editing_suggestions:
            for t in re.findall(r'"([^"]*)"', block):
                if t.strip():
                    s = t.strip()[:200]
                    editing_suggestions.append({"zh": s, "en": s})

    logger.info(
        "Recovered truncated VLM JSON via regex (dims OK, comment keys filled=%s)",
        len(dimension_comments),
    )
    return {
        "dimensions": dimensions,
        "strongest_aspect": strongest,
        "weakest_aspect": weakest,
        "tags": tags,
        "dimension_comments": dimension_comments,
        "editing_suggestions": editing_suggestions,
    }


def _clip_for_log(s: str, max_chars: int = _LOG_CLIP) -> str:
    if not s:
        return "(empty)"
    if len(s) <= max_chars:
        return s
    half = max_chars // 2
    omitted = len(s) - max_chars
    return s[:half] + f"\n... [{omitted} chars omitted] ...\n" + s[-half:]


def extract_first_json_object(text: str) -> str | None:
    """
    Return the first balanced `{...}` substring, respecting JSON string quoting.
    Avoids greedy ``first {`` to ``last }`` mistakes when multiple objects exist.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def clean_json_response(raw_text: str) -> str:
    """Strip markdown fences and isolate a single JSON object when possible."""
    s = raw_text.replace("```json", "").replace("```", "").strip()
    try:
        json.loads(s)
        return s
    except json.JSONDecodeError:
        pass
    extracted = extract_first_json_object(s)
    if extracted is not None:
        return extracted
    start_idx = s.find("{")
    end_idx = s.rfind("}")
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        return s[start_idx : end_idx + 1]
    return s


def _mirror_bilingual_pair(d: dict[str, str]) -> dict[str, str]:
    """Ensure zh and en are both filled when either side has content."""
    zh = (d.get("zh") or "").strip()
    en = (d.get("en") or "").strip()
    if zh and not en:
        en = zh
    elif en and not zh:
        zh = en
    return {"zh": zh, "en": en}


def default_stage3_parsed() -> dict[str, Any]:
    """Neutral fallback when the model output cannot be parsed (pipeline keeps running)."""
    dims = {k: float(_DEFAULT_FLOAT) for k in STAGE3_DIM_KEYS}
    empty = {"zh": "", "en": ""}
    return {
        "dimensions": dims,
        "strongest_aspect": dict(empty),
        "weakest_aspect": dict(empty),
        "tags": [],
        "dimension_comments": {},
        "editing_suggestions": [],
    }


def _explicit_model_error(data: dict[str, Any]) -> bool:
    err = data.get("error")
    return err == "invalid_output" or (isinstance(err, str) and err.strip().lower() == "invalid_output")


def _finalize_regex_recovery(recovered: dict[str, Any]) -> dict[str, Any]:
    recovered["strongest_aspect"] = _mirror_bilingual_pair(
        recovered["strongest_aspect"]
        if isinstance(recovered.get("strongest_aspect"), dict)
        else norm_bilingual_text(recovered.get("strongest_aspect"))
    )
    recovered["weakest_aspect"] = _mirror_bilingual_pair(
        recovered["weakest_aspect"]
        if isinstance(recovered.get("weakest_aspect"), dict)
        else norm_bilingual_text(recovered.get("weakest_aspect"))
    )
    es_fixed: list[dict[str, str]] = []
    for x in recovered.get("editing_suggestions", []):
        es_fixed.append(_mirror_bilingual_pair(norm_bilingual_text(x)))
    recovered["editing_suggestions"] = es_fixed
    return recovered


def default_fast_stage3_parsed() -> dict[str, Any]:
    """Neutral fast-mode fallback when JSON cannot be parsed."""
    return {
        "score": 55.0,
        "verdict": {"zh": "解析失败", "en": "Parse failure"},
        "tags": ["unparsed"],
    }


def _pydantic_validate_fast(data: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt Pydantic-validated parse of a fast Stage3 dict; return None on failure."""
    try:
        from inference.schemas import Stage3FastResponse
        return Stage3FastResponse.model_validate(data).to_parsed_dict()
    except Exception:
        return None


def _pydantic_validate_full(data: dict[str, Any]) -> dict[str, Any] | None:
    """Attempt Pydantic-validated parse of a full Stage3 dict; return None on failure."""
    try:
        from inference.schemas import Stage3FullResponse
        validated = Stage3FullResponse.model_validate(data)
        result = validated.to_parsed_dict()
        logger.debug("Stage3 Pydantic validation OK")
        return result
    except Exception as exc:
        logger.debug("Stage3 Pydantic validation skipped (%s); using manual parser", exc)
        return None


def parse_fast_vlm_response(json_str: str, raw_model_text: str | None = None) -> dict[str, Any]:
    """
    Parse compact fast Stage3 JSON: score 0-100, verdict (string or bilingual), tags.
    Returns {} on hard failure (caller may apply default_fast_stage3_parsed).
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        extracted = extract_first_json_object(json_str)
        if extracted:
            try:
                data = json.loads(extracted)
            except json.JSONDecodeError:
                logger.warning("Fast VLM JSON parse failed | cleaned_len=%s", len(json_str))
                return {}
        else:
            logger.warning("Fast VLM JSON parse failed | cleaned_len=%s", len(json_str))
            return {}

    if not isinstance(data, dict):
        return {}

    if _explicit_model_error(data):
        return {}

    pydantic_result = _pydantic_validate_fast(data)
    if pydantic_result is not None:
        return pydantic_result

    raw_score = data.get("score", _DEFAULT_FLOAT * 10.0)
    try:
        score_f = float(raw_score)
    except (TypeError, ValueError):
        score_f = 55.0
    score_f = max(0.0, min(100.0, score_f))

    verdict = _mirror_bilingual_pair(norm_bilingual_text(data.get("verdict", "")))

    tags_raw = data.get("tags", [])
    tags: list[str] = []
    if isinstance(tags_raw, list):
        for t in tags_raw:
            if isinstance(t, str) and t.strip():
                tags.append(t.strip()[:80])
            elif t is not None:
                s = str(t).strip()
                if s:
                    tags.append(s[:80])

    return {
        "score": score_f,
        "verdict": verdict,
        "tags": tags,
    }


def parse_dimensional_response(json_str: str, raw_model_text: str | None = None) -> dict[str, Any]:
    """
    Parse stage3 dimensional JSON while preserving existing output contract.

    On failure returns ``{}``. Uses regex recovery only as a last resort after structured extraction fails.
    """
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse JSON response: %s | cleaned_len=%s raw_len=%s",
            e,
            len(json_str),
            len(raw_model_text or ""),
        )
        logger.warning("--- cleaned (string passed to json.loads) ---\n%s", _clip_for_log(json_str))
        if raw_model_text is not None and raw_model_text.strip() != json_str.strip():
            logger.warning("--- raw model output (before clean_json_response) ---\n%s", _clip_for_log(raw_model_text))

        recovered = _fallback_parse_truncated_json(json_str)
        if recovered:
            return _finalize_regex_recovery(recovered)
        return {}

    if not isinstance(data, dict):
        recovered = _fallback_parse_truncated_json(json_str)
        return _finalize_regex_recovery(recovered) if recovered else {}

    pydantic_result = _pydantic_validate_full(data)
    if pydantic_result is not None:
        return pydantic_result

    try:
        if _explicit_model_error(data):
            logger.warning("Model returned explicit invalid_output sentinel")
            return {}

        dimensions = {}
        for dim in STAGE3_DIM_KEYS:
            score = data.get(dim, _DEFAULT_FLOAT)
            try:
                score = float(score)
                score = max(0, min(10, score))
            except (ValueError, TypeError):
                score = _DEFAULT_FLOAT
            dimensions[dim] = score

        raw_comments = data.get("comments", {})
        if not isinstance(raw_comments, dict):
            raw_comments = {}
        dimension_comments: dict[str, dict[str, str]] = {}
        for dim in STAGE3_DIM_KEYS:
            cm = _mirror_bilingual_pair(norm_bilingual_text(raw_comments.get(dim, "")))
            if cm["zh"] or cm["en"]:
                dimension_comments[dim] = cm

        raw_edit = data.get("editing_suggestions", [])
        editing_suggestions: list[dict[str, str]] = []
        if isinstance(raw_edit, list):
            for x in raw_edit[:5]:
                item = _norm_edit_item(x)
                if item:
                    editing_suggestions.append(_mirror_bilingual_pair(item))

        sa = _mirror_bilingual_pair(norm_bilingual_text(data.get("strongest_aspect", "")))
        wa = _mirror_bilingual_pair(norm_bilingual_text(data.get("weakest_aspect", "")))

        tags_raw = data.get("tags", [])
        tags: list[str] = []
        if isinstance(tags_raw, list):
            for t in tags_raw:
                if isinstance(t, str) and t.strip():
                    tags.append(t.strip())
                elif t is not None:
                    tags.append(str(t).strip())

        return {
            "dimensions": dimensions,
            "strongest_aspect": sa,
            "tags": tags,
            "weakest_aspect": wa,
            "dimension_comments": dimension_comments,
            "editing_suggestions": editing_suggestions,
        }
    except Exception as e:
        logger.error("Error parsing dimensional response: %s", e)
        return {}


def parse_editing_suggestions_response(json_str: str) -> list[dict[str, str]]:
    """Parse Stage4 JSON: ``{"editing_suggestions": [{"zh","en"}, ...]}``."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("editing_suggestions", [])
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for x in raw[:5]:
        item = _norm_edit_item(x)
        if item:
            out.append(_mirror_bilingual_pair(item))
    return out
