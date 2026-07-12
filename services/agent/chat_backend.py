"""Chat-completions backend for the conversational agent.

:class:`~services.agent.conversation.ConversationalAgent` needs a
``ChatFn = (messages) -> str``. This builds one over the same ``model.*`` section of
``configs/livehouse.yaml`` the rest of the system uses, so the chat "brain" rides the
production provider config rather than a parallel path:

- ``provider: ollama``        → ``POST /api/chat``            (message list, no image)
- ``provider: vllm | openai`` → ``POST /v1/chat/completions`` (message list, no image)
- ``provider: mock``          → unsupported (caller should not build a chat agent)

Transport / HTTP errors are raised on purpose; the API layer turns them into a friendly
error turn instead of crashing the request.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator, Mapping, Optional

import requests

from inference.providers.ollama import resolve_ollama_base_urls
from inference.providers.vllm import chat_completions_url, resolve_vllm_base_urls
from services.agent.conversation import ChatFn, StreamChatFn

logger = logging.getLogger(__name__)

DEFAULT_CHAT_NUM_PREDICT = 512
DEFAULT_CHAT_TEMPERATURE = 0.3
DEFAULT_CHAT_TIMEOUT = 90


def _http_timeout(timeout: int) -> tuple[int, int]:
    t = max(5, int(timeout))
    return (min(30, max(5, t // 4)), t)


def _ollama_chat_fn(*, endpoint: str, model_name: str, temperature: float, num_predict: int, timeout: int) -> ChatFn:
    url = f"{endpoint.rstrip('/')}/api/chat"

    def _chat(messages: list[dict[str, str]]) -> str:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        resp = requests.post(url, json=payload, timeout=_http_timeout(timeout))
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        return str(msg.get("content", "") or "").strip()

    return _chat


def _openai_chat_fn(
    *, endpoint: str, model_name: str, temperature: float, num_predict: int, timeout: int, api_key: Optional[str]
) -> ChatFn:
    url = chat_completions_url(endpoint)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _chat(messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
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

    return _chat


def build_chat_fn(
    model_config: Mapping[str, Any],
    *,
    num_predict: int = DEFAULT_CHAT_NUM_PREDICT,
    temperature: float = DEFAULT_CHAT_TEMPERATURE,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
) -> ChatFn:
    """Build a ``ChatFn`` from a model-section dict. Raises ``ValueError`` for ``mock``."""
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    model_name = str(model_name or model_config.get("model_name") or "llava").strip()
    eff_timeout = int(timeout if timeout is not None else min(int(model_config.get("timeout", 120) or 120), DEFAULT_CHAT_TIMEOUT))

    if provider == "mock":
        raise ValueError("provider 'mock' has no chat backend")

    if provider in ("vllm", "openai"):
        urls = resolve_vllm_base_urls(model_config)
        return _openai_chat_fn(
            endpoint=urls[0],
            model_name=model_name,
            temperature=temperature,
            num_predict=num_predict,
            timeout=eff_timeout,
            api_key=(model_config.get("api_key") or None),
        )

    urls = resolve_ollama_base_urls(model_config)
    return _ollama_chat_fn(
        endpoint=urls[0],
        model_name=model_name,
        temperature=temperature,
        num_predict=num_predict,
        timeout=eff_timeout,
    )


def build_chat_fn_from_config(
    config_path: str = "configs/livehouse.yaml", *, model_name: Optional[str] = None
) -> ChatFn:
    """Convenience: load the model section from a config file and build a ChatFn."""
    from utils.config_loader import ConfigLoader

    model_config = ConfigLoader.get_model_config(ConfigLoader.load(config_path))
    return build_chat_fn(model_config, model_name=model_name)


def _ollama_stream_chat_fn(
    *, endpoint: str, model_name: str, temperature: float, num_predict: int, timeout: int
) -> StreamChatFn:
    url = f"{endpoint.rstrip('/')}/api/chat"

    def _stream(messages: list[dict[str, str]]) -> Iterator[str]:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        with requests.post(url, json=payload, timeout=_http_timeout(timeout), stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                piece = str((obj.get("message") or {}).get("content", "") or "")
                if piece:
                    yield piece
                if obj.get("done"):
                    break

    return _stream


def _openai_stream_chat_fn(
    *, endpoint: str, model_name: str, temperature: float, num_predict: int, timeout: int, api_key: Optional[str]
) -> StreamChatFn:
    url = chat_completions_url(endpoint)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    def _stream(messages: list[dict[str, str]]) -> Iterator[str]:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": num_predict,
            "temperature": temperature,
            "stream": True,
        }
        with requests.post(url, json=payload, headers=headers, timeout=_http_timeout(timeout), stream=True) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if not line or line == "[DONE]":
                    if line == "[DONE]":
                        break
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if isinstance(content, list):  # content parts
                    content = "".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
                if content:
                    yield str(content)

    return _stream


def build_stream_chat_fn(
    model_config: Mapping[str, Any],
    *,
    num_predict: int = DEFAULT_CHAT_NUM_PREDICT,
    temperature: float = DEFAULT_CHAT_TEMPERATURE,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
) -> StreamChatFn:
    """Build a streaming ``StreamChatFn`` mirroring :func:`build_chat_fn`.

    Raises ``ValueError`` for ``provider: mock`` (no chat backend). The caller may
    treat a build failure as "no streaming" and fall back to the non-streaming path.
    """
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    model_name = str(model_name or model_config.get("model_name") or "llava").strip()
    eff_timeout = int(timeout if timeout is not None else min(int(model_config.get("timeout", 120) or 120), DEFAULT_CHAT_TIMEOUT))

    if provider == "mock":
        raise ValueError("provider 'mock' has no chat backend")

    if provider in ("vllm", "openai"):
        urls = resolve_vllm_base_urls(model_config)
        return _openai_stream_chat_fn(
            endpoint=urls[0],
            model_name=model_name,
            temperature=temperature,
            num_predict=num_predict,
            timeout=eff_timeout,
            api_key=(model_config.get("api_key") or None),
        )

    urls = resolve_ollama_base_urls(model_config)
    return _ollama_stream_chat_fn(
        endpoint=urls[0],
        model_name=model_name,
        temperature=temperature,
        num_predict=num_predict,
        timeout=eff_timeout,
    )
