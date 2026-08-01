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

Experimental native tool calling
--------------------------------
Default production path still relies on the text protocol
(``{"tool": name, "args": {...}}`` in ``content``) + regex/JSON parse.

Set ``LIVEHOUSE_AGENT_NATIVE_TOOLS=1`` and pass ``tools=registry.tool_specs()`` into
:func:`build_chat_fn` to also send OpenAI-shaped ``tools`` on the wire (Ollama
qwen2.5 / vLLM). When the model returns native ``tool_calls``, they are serialized
back into the same text-protocol JSON so :func:`_parse_tool_call` and the decide→act
loop stay unchanged — enabling A/B without replacing the production path.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator, Mapping, Optional

import requests

from inference.providers.ollama import resolve_ollama_base_urls
from inference.providers.vllm import chat_completions_url, resolve_vllm_base_urls
from services.agent.conversation import ChatFn, StreamChatFn
from services.agent.openai_adapter import _parse_arguments

logger = logging.getLogger(__name__)

DEFAULT_CHAT_NUM_PREDICT = 512
DEFAULT_CHAT_TEMPERATURE = 0.3
DEFAULT_CHAT_TIMEOUT = 90

# Opt-in: attach OpenAI-shaped ``tools`` to chat requests (Ollama / vLLM native FC).
NATIVE_TOOLS_ENV = "LIVEHOUSE_AGENT_NATIVE_TOOLS"


def native_tools_enabled(explicit: Optional[bool] = None) -> bool:
    """Return whether native ``tools=[...]`` should be attached to chat requests.

    ``explicit`` wins when not ``None``; otherwise read ``LIVEHOUSE_AGENT_NATIVE_TOOLS``.
    """
    if explicit is not None:
        return bool(explicit)
    raw = (os.environ.get(NATIVE_TOOLS_ENV) or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _http_timeout(timeout: int) -> tuple[int, int]:
    t = max(5, int(timeout))
    return (min(30, max(5, t // 4)), t)


def normalize_native_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize Ollama/OpenAI ``message.tool_calls`` to ``[{tool, args}, ...]``."""
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        return []
    out: list[dict[str, Any]] = []
    for tc in raw_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        args = _parse_arguments(fn.get("arguments") if "arguments" in fn else fn.get("args"))
        out.append({"tool": name, "args": args})
    return out


def content_from_assistant_message(message: Mapping[str, Any]) -> str:
    """Bridge native ``tool_calls`` into the text-protocol JSON the agent already parses.

    Priority: first native tool_call → ``{"tool","args"}`` string; else ``content``.
    """
    calls = normalize_native_tool_calls(message)
    if calls:
        return json.dumps(
            {"tool": calls[0]["tool"], "args": calls[0].get("args") or {}},
            ensure_ascii=False,
        )
    content = message.get("content")
    if isinstance(content, list):  # some servers return content parts
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict)).strip()
    return str(content or "").strip()


def _ollama_chat_fn(
    *,
    endpoint: str,
    model_name: str,
    temperature: float,
    num_predict: int,
    timeout: int,
    tools: Optional[list[dict[str, Any]]] = None,
) -> ChatFn:
    url = f"{endpoint.rstrip('/')}/api/chat"

    def _chat(messages: list[dict[str, str]]) -> str:
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": num_predict},
        }
        if tools:
            payload["tools"] = tools
        resp = requests.post(url, json=payload, timeout=_http_timeout(timeout))
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
        return content_from_assistant_message(msg if isinstance(msg, dict) else {})

    return _chat


def _openai_chat_fn(
    *,
    endpoint: str,
    model_name: str,
    temperature: float,
    num_predict: int,
    timeout: int,
    api_key: Optional[str],
    tools: Optional[list[dict[str, Any]]] = None,
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
        if tools:
            payload["tools"] = tools
        resp = requests.post(url, json=payload, headers=headers, timeout=_http_timeout(timeout))
        resp.raise_for_status()
        choices = resp.json().get("choices") or []
        if not choices:
            return ""
        msg = choices[0].get("message") or {}
        return content_from_assistant_message(msg if isinstance(msg, dict) else {})

    return _chat


def build_chat_fn(
    model_config: Mapping[str, Any],
    *,
    num_predict: int = DEFAULT_CHAT_NUM_PREDICT,
    temperature: float = DEFAULT_CHAT_TEMPERATURE,
    timeout: Optional[int] = None,
    model_name: Optional[str] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    native_tools: Optional[bool] = None,
) -> ChatFn:
    """Build a ``ChatFn`` from a model-section dict. Raises ``ValueError`` for ``mock``.

    When native tools are enabled (``native_tools=True`` or ``LIVEHOUSE_AGENT_NATIVE_TOOLS``)
    and ``tools`` is a non-empty OpenAI-shaped list (from ``SkillRegistry.tool_specs()``),
    the request includes ``tools``. Native ``tool_calls`` are bridged to text-protocol JSON.
    """
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    model_name = str(model_name or model_config.get("model_name") or "llava").strip()
    eff_timeout = int(timeout if timeout is not None else min(int(model_config.get("timeout", 120) or 120), DEFAULT_CHAT_TIMEOUT))
    use_tools = list(tools) if (native_tools_enabled(native_tools) and tools) else None
    if native_tools_enabled(native_tools) and not tools:
        logger.warning(
            "%s is set but no tools= were passed to build_chat_fn; falling back to text protocol",
            NATIVE_TOOLS_ENV,
        )

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
            tools=use_tools,
        )

    urls = resolve_ollama_base_urls(model_config)
    return _ollama_chat_fn(
        endpoint=urls[0],
        model_name=model_name,
        temperature=temperature,
        num_predict=num_predict,
        timeout=eff_timeout,
        tools=use_tools,
    )


def build_chat_fn_from_config(
    config_path: str = "configs/livehouse.yaml",
    *,
    model_name: Optional[str] = None,
    tools: Optional[list[dict[str, Any]]] = None,
    native_tools: Optional[bool] = None,
) -> ChatFn:
    """Convenience: load the model section from a config file and build a ChatFn."""
    from utils.config_loader import ConfigLoader

    model_config = ConfigLoader.get_model_config(ConfigLoader.load(config_path))
    return build_chat_fn(
        model_config, model_name=model_name, tools=tools, native_tools=native_tools
    )


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

    Note: native ``tools`` are intentionally not attached on the stream path yet —
    Ollama/OpenAI stream tool_call deltas differently; use the non-stream ChatFn for A/B.
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
