"""Text-completion backend that wires :class:`LLMPlanner` to a real model.

The planner only needs a ``CompleteFn = (prompt: str) -> str`` (see
``services/agent/planner.py``); this module builds one over the *same* model
section of ``configs/livehouse.yaml`` the rest of the system uses, so the agent's
"brain" rides the production provider config instead of a parallel path:

- ``provider: ollama``         → ``POST /api/generate`` (text-only, no image)
- ``provider: vllm | openai``  → ``POST /v1/chat/completions`` (text-only)
- ``provider: mock``           → unsupported (callers should keep the heuristic
  planner; there is no useful LLM brain to mock here)

The completion fn raises on transport / HTTP errors **on purpose**: ``LLMPlanner``
catches any exception and falls back to the deterministic ``HeuristicPlanner``,
which is the structured-output-reliability contract the planner already expects.
A planner LLM should answer fast, so the defaults are a short token budget, low
temperature, and a tight timeout — much smaller than the VLM scoring calls.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

import requests

from inference.providers.ollama import resolve_ollama_base_urls
from inference.providers.vllm import chat_completions_url, resolve_vllm_base_urls
from services.agent.planner import (
    CompleteFn,
    HeuristicPlanner,
    LLMPlanner,
    Planner,
    StratifiedHeuristicPlanner,
)

logger = logging.getLogger(__name__)

# Planner decisions are tiny JSON objects; keep generations short, cheap, and near-greedy.
DEFAULT_PLANNER_NUM_PREDICT = 192
DEFAULT_PLANNER_TEMPERATURE = 0.2
DEFAULT_PLANNER_TIMEOUT = 60


def _http_timeout(timeout: int) -> tuple[int, int]:
    t = max(5, int(timeout))
    return (min(30, max(5, t // 4)), t)


def _ollama_complete_fn(
    *,
    endpoint: str,
    model_name: str,
    temperature: float,
    num_predict: int,
    timeout: int,
) -> CompleteFn:
    url = f"{endpoint.rstrip('/')}/api/generate"

    def _complete(prompt: str) -> str:
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        resp = requests.post(url, json=payload, timeout=_http_timeout(timeout))
        resp.raise_for_status()
        return str(resp.json().get("response", "") or "").strip()

    return _complete


def _openai_complete_fn(
    *,
    endpoint: str,
    model_name: str,
    temperature: float,
    num_predict: int,
    timeout: int,
    api_key: Optional[str],
) -> CompleteFn:
    url = chat_completions_url(endpoint)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _complete(prompt: str) -> str:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": num_predict,
            "temperature": temperature,
            "stream": False,
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=_http_timeout(timeout))
        resp.raise_for_status()
        choices = resp.json().get("choices") or []
        if not choices:
            return ""
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):  # some servers return content parts
            return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict)).strip()
        return str(content or "").strip()

    return _complete


def build_planner_complete_fn(
    model_config: Mapping[str, Any],
    *,
    num_predict: int = DEFAULT_PLANNER_NUM_PREDICT,
    temperature: float = DEFAULT_PLANNER_TEMPERATURE,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
) -> CompleteFn:
    """Build a text ``CompleteFn`` from a model-section dict (yaml / ``ConfigLoader``).

    ``model_name`` overrides the config's model (e.g. point the *planner* at a small
    instruct text model while the *scoring* path keeps the VLM). Raises ``ValueError``
    for ``provider: mock`` (no meaningful planner LLM) so the caller falls back to the
    heuristic planner explicitly rather than silently.
    """
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    model_name = str(model_name or model_config.get("model_name") or "llava").strip()
    # A planner reasons fast; cap the inherited (image-sized) timeout to keep the loop snappy.
    eff_timeout = int(timeout if timeout is not None else min(int(model_config.get("timeout", 120) or 120), DEFAULT_PLANNER_TIMEOUT))

    if provider == "mock":
        raise ValueError("provider 'mock' has no planner LLM; keep the heuristic planner")

    if provider in ("vllm", "openai"):
        urls = resolve_vllm_base_urls(model_config)
        return _openai_complete_fn(
            endpoint=urls[0],
            model_name=model_name,
            temperature=temperature,
            num_predict=num_predict,
            timeout=eff_timeout,
            api_key=(model_config.get("api_key") or None),
        )

    urls = resolve_ollama_base_urls(model_config)
    return _ollama_complete_fn(
        endpoint=urls[0],
        model_name=model_name,
        temperature=temperature,
        num_predict=num_predict,
        timeout=eff_timeout,
    )


def build_curation_llm_planner(
    model_config: Mapping[str, Any],
    *,
    fallback: Optional[Planner] = None,
    num_predict: int = DEFAULT_PLANNER_NUM_PREDICT,
    temperature: float = DEFAULT_PLANNER_TEMPERATURE,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
    max_state_candidates: int = 40,
) -> Planner:
    """LLM tool-calling planner over the configured provider, heuristic fallback.

    Returns :class:`StratifiedHeuristicPlanner` when the provider is ``mock`` or
    the backend cannot be built, so callers can wire this unconditionally.
    """
    fb = fallback or StratifiedHeuristicPlanner()
    try:
        complete_fn = build_planner_complete_fn(
            model_config,
            num_predict=num_predict,
            temperature=temperature,
            timeout=timeout,
            model_name=model_name,
        )
    except ValueError as exc:
        logger.info("curation LLM planner unavailable (%s); using heuristic planner", exc)
        return fb
    return LLMPlanner(complete_fn, fallback=fb, max_state_candidates=max_state_candidates)


def build_curation_llm_planner_from_config(
    config_path: str = "configs/livehouse.yaml",
    *,
    fallback: Optional[Planner] = None,
    model_name: Optional[str] = None,
    max_state_candidates: int = 40,
) -> Planner:
    """Convenience: load the model section from a config file and build the planner.

    When the caller does not pin ``model_name``, the planner defaults to the dedicated
    instruct model (``model.agent_chat_model``) rather than the VLM ``model_name``: the
    planner does text tool-calling, and a vision model (e.g. ``llava``) follows the JSON
    action protocol poorly, which would silently inflate the heuristic fallback rate.
    Empty ``agent_chat_model`` reuses the config ``model_name`` inside the backend.
    """
    from utils.config_loader import ConfigLoader

    model_config = ConfigLoader.get_model_config(ConfigLoader.load(config_path))
    if model_name is None:
        model_name = str(model_config.get("agent_chat_model") or "").strip() or None
    return build_curation_llm_planner(
        model_config, fallback=fallback, model_name=model_name, max_state_candidates=max_state_candidates
    )
