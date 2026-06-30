"""Text-only Ollama resolver when keyword rules miss (``rules:fallback``)."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import requests

from inference.parsers import clean_json_response
from inference.providers.ollama import resolve_ollama_base_urls
from services.film_render_service import FILM_VARIANT_IDS
from services.vibe_film_policy import FilmVibeDecision, _VIBE_RULES

logger = logging.getLogger(__name__)

_VARIANT_LABELS: dict[str, str] = {row[1]: row[2] for row in _VIBE_RULES}


def _vibe_film_cfg() -> dict[str, Any]:
    from utils.config_loader import ConfigLoader

    proc = ConfigLoader.load().get("processing") or {}
    vf = proc.get("vibe_film") if isinstance(proc.get("vibe_film"), dict) else {}
    return vf if isinstance(vf, dict) else {}


def llm_on_miss_enabled() -> bool:
    env = os.getenv("LIVEHOUSE_VIBE_LLM_ON_MISS", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return bool(_vibe_film_cfg().get("llm_on_miss", True))


def _model_settings() -> tuple[str, str, float, int, int]:
    from utils.config_loader import ConfigLoader

    model = ConfigLoader.get_model_config(ConfigLoader.load())
    vf = _vibe_film_cfg()
    urls = resolve_ollama_base_urls(model)
    endpoint = urls[0] if urls else "http://localhost:11434"
    name = str(vf.get("text_model_name") or model.get("model_name") or "llava").strip()
    temperature = float(vf.get("llm_temperature", 0.15) or 0.15)
    num_predict = int(vf.get("llm_num_predict", 160) or 160)
    timeout = int(vf.get("llm_timeout", 90) or 90)
    return endpoint, name, temperature, num_predict, timeout


def _build_vibe_llm_prompt(user_prompt: str) -> str:
    ids = ", ".join(f'"{v}"' for v in FILM_VARIANT_IDS)
    catalog = "\n".join(f'  - "{vid}": {label}' for vid, label in _VARIANT_LABELS.items())
    return (
        "You are a photo color-grade router. Map the user's vibe description to exactly ONE preset id.\n"
        f"Allowed film_variant values: {ids}\n"
        "Catalog:\n"
        f"{catalog}\n\n"
        f'User vibe (Chinese or English): "{user_prompt.strip()}"\n\n'
        "Reply with ONLY one JSON object, no markdown:\n"
        '{"film_variant": "<id>", "reason_zh": "<short Chinese why>"}\n'
    )


def ollama_generate_text(
    *,
    endpoint: str,
    model_name: str,
    prompt: str,
    temperature: float,
    num_predict: int,
    timeout: int,
) -> str:
    url = f"{endpoint.rstrip('/')}/api/generate"
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    connect_t = min(30, max(5, timeout // 4))
    response = requests.post(url, json=payload, timeout=(connect_t, max(5, timeout)))
    response.raise_for_status()
    return str(response.json().get("response", "") or "").strip()


def _parse_llm_vibe_json(raw_text: str) -> dict[str, Any] | None:
    clean = clean_json_response(raw_text)
    try:
        data = json.loads(clean)
    except json.JSONDecodeError:
        m = re.search(r"\{[^{}]*\"film_variant\"[^{}]*\}", clean, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def try_resolve_vibe_via_llm(user_prompt: str) -> FilmVibeDecision | None:
    """Return a decision when Ollama returns a valid ``film_variant``; else None."""
    if not user_prompt.strip():
        return None
    if not llm_on_miss_enabled():
        return None
    try:
        endpoint, model_name, temperature, num_predict, timeout = _model_settings()
        llm_prompt = _build_vibe_llm_prompt(user_prompt)
        raw = ollama_generate_text(
            endpoint=endpoint,
            model_name=model_name,
            prompt=llm_prompt,
            temperature=temperature,
            num_predict=num_predict,
            timeout=timeout,
        )
    except Exception as e:
        logger.warning("vibe LLM resolve failed: %s", e)
        return None

    data = _parse_llm_vibe_json(raw)
    if not data:
        return None
    variant = str(data.get("film_variant") or "").strip()
    if variant not in FILM_VARIANT_IDS:
        return None
    label = _VARIANT_LABELS.get(variant, variant)
    reason = str(data.get("reason_zh") or "").strip() or "大模型根据描述选择胶片预设"
    return FilmVibeDecision(
        film_variant=variant,
        label_zh=label,
        reason_zh=f"{reason}（AI 解析）",
        matched_by="llm:ollama",
        prompt=user_prompt.strip(),
        matched=True,
    )
