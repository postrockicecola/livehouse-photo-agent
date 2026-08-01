"""Text-protocol tool-call parse regressions (Gallery agent L0)."""
from __future__ import annotations

import json

import pytest

from services.agent.chat_backend import content_from_assistant_message
from services.agent.conversation import _parse_tool_call

_TABLE: list[tuple[str, str, dict | None]] = [
    (
        "bare_json",
        '{"tool": "gallery_search", "args": {"query": "鼓手", "limit": 5}}',
        {"tool": "gallery_search", "args": {"query": "鼓手", "limit": 5}},
    ),
    (
        "fence_json",
        '```json\n{"tool":"t","args":{}}\n```',
        {"tool": "t", "args": {}},
    ),
    (
        "fence_no_lang",
        '```\n{"tool":"gallery_stats","args":{}}\n```',
        {"tool": "gallery_stats", "args": {}},
    ),
    (
        "prose_wrap",
        '好的，我调用工具：{"tool":"explain_photo","args":{"file":"a.jpg"}} 完成。',
        {"tool": "explain_photo", "args": {"file": "a.jpg"}},
    ),
    (
        "missing_args_key",
        '{"tool": "gallery_stats"}',
        {"tool": "gallery_stats", "args": {}},
    ),
    (
        "plain_prose",
        "本场没有萨克斯相关命中。",
        None,
    ),
    (
        "no_tool_field",
        '{"name": "gallery_search", "args": {}}',
        None,
    ),
    (
        "malformed",
        '{"tool": "gallery_search", "args": {',
        None,
    ),
    (
        "empty",
        "",
        None,
    ),
    (
        "two_objects_concat",
        '{"tool":"a","args":{}}{"tool":"b","args":{}}',
        None,  # substring from first { to last } is invalid JSON
    ),
    (
        "args_with_nested",
        '{"tool":"gallery_select","args":{"files":["a.jpg","b.jpg"],"note":"x{y}"}}',
        {"tool": "gallery_select", "args": {"files": ["a.jpg", "b.jpg"], "note": "x{y}"}},
    ),
]


@pytest.mark.parametrize("case_id,raw,expect", _TABLE, ids=[t[0] for t in _TABLE])
def test_parse_tool_call_table(case_id: str, raw: str, expect: dict | None) -> None:
    _ = case_id
    assert _parse_tool_call(raw) == expect


def test_native_bridge_roundtrip_single_call() -> None:
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "function": {
                    "name": "gallery_search",
                    "arguments": json.dumps({"query": "鼓手", "limit": 3}),
                }
            }
        ],
    }
    bridged = content_from_assistant_message(msg)
    assert _parse_tool_call(bridged) == {
        "tool": "gallery_search",
        "args": {"query": "鼓手", "limit": 3},
    }


def test_native_bridge_prefers_first_tool_call() -> None:
    msg = {
        "tool_calls": [
            {"function": {"name": "gallery_search", "arguments": "{}"}},
            {"function": {"name": "gallery_select", "arguments": "{}"}},
        ]
    }
    bridged = content_from_assistant_message(msg)
    parsed = _parse_tool_call(bridged)
    assert parsed is not None
    assert parsed["tool"] == "gallery_search"
