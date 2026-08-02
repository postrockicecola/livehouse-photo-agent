"""Unit tests for experimental native tool calling bridge in chat_backend."""
from __future__ import annotations

import json

import pytest

from services.agent.chat_backend import (
    NATIVE_TOOLS_ENV,
    content_from_assistant_message,
    native_tools_enabled,
    normalize_native_tool_calls,
)
from services.agent.conversation import _parse_tool_call


def test_native_tools_enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(NATIVE_TOOLS_ENV, raising=False)
    # Default auto: off without capable provider, on for openai/vllm.
    assert native_tools_enabled() is False
    assert native_tools_enabled(provider="ollama") is False
    assert native_tools_enabled(provider="openai") is True
    assert native_tools_enabled(provider="vllm") is True
    monkeypatch.setenv(NATIVE_TOOLS_ENV, "1")
    assert native_tools_enabled(provider="ollama") is True
    monkeypatch.setenv(NATIVE_TOOLS_ENV, "0")
    assert native_tools_enabled(provider="openai") is False
    assert native_tools_enabled(False) is False
    assert native_tools_enabled(True) is True


def test_normalize_ollama_dict_arguments() -> None:
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "gallery_search",
                    "arguments": {"query": "鼓手", "limit": 5},
                }
            }
        ],
    }
    calls = normalize_native_tool_calls(msg)
    assert calls == [{"tool": "gallery_search", "args": {"query": "鼓手", "limit": 5}}]


def test_normalize_openai_string_arguments() -> None:
    msg = {
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "gallery_stats",
                    "arguments": '{"category":"best"}',
                },
            }
        ]
    }
    assert normalize_native_tool_calls(msg)[0]["args"] == {"category": "best"}


def test_content_bridge_roundtrips_parse_tool_call() -> None:
    msg = {
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "gallery_search",
                    "arguments": {"exclude_trash": True, "limit": 10},
                }
            }
        ],
    }
    text = content_from_assistant_message(msg)
    parsed = _parse_tool_call(text)
    assert parsed == {
        "tool": "gallery_search",
        "args": {"exclude_trash": True, "limit": 10},
    }


def test_content_falls_back_to_text_when_no_tool_calls() -> None:
    assert content_from_assistant_message({"content": "  hello  ", "tool_calls": []}) == "hello"
    # Malformed JSON in content is left as-is (parse layer decides).
    raw = content_from_assistant_message({"content": "not a tool"})
    assert _parse_tool_call(raw) is None


def test_content_bridge_packs_multiple_native_calls() -> None:
    from services.agent.tool_protocol import MULTI_TOOL, expand_tool_calls

    msg = {
        "tool_calls": [
            {"function": {"name": "gallery_stats", "arguments": {}}},
            {"function": {"name": "gallery_search", "arguments": {"query": "鼓手", "limit": 3}}},
        ]
    }
    text = content_from_assistant_message(msg)
    parsed = _parse_tool_call(text)
    assert parsed is not None
    assert parsed["tool"] == MULTI_TOOL
    expanded = expand_tool_calls(parsed)
    assert [c["tool"] for c in expanded] == ["gallery_stats", "gallery_search"]


def test_build_chat_fn_attaches_tools_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.agent import chat_backend as cb

    captured: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "gallery_stats",
                                "arguments": {},
                            }
                        }
                    ],
                }
            }

    def _post(url, json=None, timeout=None, **_kw):  # noqa: A002
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(cb.requests, "post", _post)
    monkeypatch.setenv(NATIVE_TOOLS_ENV, "1")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "gallery_stats",
                "description": "stats",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    fn = cb.build_chat_fn(
        {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model_name": "qwen2.5:3b-instruct"},
        tools=tools,
        native_tools=True,
    )
    out = fn([{"role": "user", "content": "概况"}])
    assert "tools" in captured["payload"]
    assert captured["payload"]["tools"][0]["function"]["name"] == "gallery_stats"
    assert json.loads(out)["tool"] == "gallery_stats"


def test_build_chat_fn_omits_tools_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.agent import chat_backend as cb

    captured: dict = {}

    class _Resp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"message": {"content": '{"tool":"gallery_stats","args":{}}'}}

    def _post(url, json=None, timeout=None, **_kw):  # noqa: A002
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(cb.requests, "post", _post)
    monkeypatch.delenv(NATIVE_TOOLS_ENV, raising=False)
    tools = [{"type": "function", "function": {"name": "gallery_stats", "parameters": {}}}]
    fn = cb.build_chat_fn(
        {"provider": "ollama", "endpoint": "http://127.0.0.1:11434", "model_name": "x"},
        tools=tools,
        native_tools=False,
    )
    fn([{"role": "user", "content": "hi"}])
    assert "tools" not in captured["payload"]
