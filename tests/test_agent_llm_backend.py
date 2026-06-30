"""Tests for the curation agent's LLM planner backend (services/agent/llm_backend).

These verify the text-completion adapter that wires :class:`LLMPlanner` to a real
provider, without any network: ``requests.post`` is monkeypatched. Covered:

- ollama provider builds an ``/api/generate`` text-only call and returns ``response``;
- vllm/openai provider builds a ``/v1/chat/completions`` call (+ bearer auth) and
  returns the message content;
- ``provider: mock`` yields the heuristic planner (no LLM brain);
- a completion that raises propagates so ``LLMPlanner`` can fall back.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.agent import llm_backend
from services.agent.planner import HeuristicPlanner, LLMPlanner


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_ollama_complete_fn_builds_generate_call(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002 - mirror requests API
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResp({"response": '{"action": "finalize", "reason": "done"}'})

    monkeypatch.setattr(llm_backend.requests, "post", fake_post)

    fn = llm_backend.build_planner_complete_fn(
        {"provider": "ollama", "endpoint": "http://localhost:11434", "model_name": "llava"}
    )
    out = fn("decide next action")

    assert out == '{"action": "finalize", "reason": "done"}'
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["json"]["model"] == "llava"
    assert captured["json"]["prompt"] == "decide next action"
    assert "images" not in captured["json"]  # text-only, no image attached
    assert captured["json"]["stream"] is False


def test_openai_complete_fn_builds_chat_call_with_auth(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResp({"choices": [{"message": {"content": '{"action": "inspect"}'}}]})

    monkeypatch.setattr(llm_backend.requests, "post", fake_post)

    fn = llm_backend.build_planner_complete_fn(
        {
            "provider": "vllm",
            "endpoint": "http://localhost:8000",
            "model_name": "Qwen/Qwen2-VL-7B-Instruct",
            "api_key": "sk-test",
        }
    )
    out = fn("decide")

    assert out == '{"action": "inspect"}'
    assert captured["url"] == "http://localhost:8000/v1/chat/completions"
    assert captured["json"]["messages"] == [{"role": "user", "content": "decide"}]
    assert captured["headers"]["Authorization"] == "Bearer sk-test"


def test_openai_complete_fn_handles_content_parts(monkeypatch):
    def fake_post(url, json=None, timeout=None, headers=None):  # noqa: A002
        return _FakeResp(
            {"choices": [{"message": {"content": [{"text": "{\"action\":"}, {"text": " \"finalize\"}"}]}}]}
        )

    monkeypatch.setattr(llm_backend.requests, "post", fake_post)
    fn = llm_backend.build_planner_complete_fn(
        {"provider": "openai", "endpoint": "http://x/v1", "model_name": "gpt"}
    )
    assert fn("p") == '{"action": "finalize"}'


def test_mock_provider_has_no_planner_llm():
    with pytest.raises(ValueError):
        llm_backend.build_planner_complete_fn({"provider": "mock", "model_name": "mock-vlm"})


def test_build_curation_llm_planner_falls_back_to_heuristic_for_mock():
    planner = llm_backend.build_curation_llm_planner({"provider": "mock"})
    assert isinstance(planner, HeuristicPlanner)


def test_build_curation_llm_planner_returns_llm_planner(monkeypatch):
    monkeypatch.setattr(
        llm_backend.requests,
        "post",
        lambda *a, **k: _FakeResp({"response": "{}"}),
    )
    planner = llm_backend.build_curation_llm_planner(
        {"provider": "ollama", "endpoint": "http://localhost:11434", "model_name": "llava"}
    )
    assert isinstance(planner, LLMPlanner)


def test_complete_fn_error_propagates_for_planner_fallback(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(llm_backend.requests, "post", boom)
    fn = llm_backend.build_planner_complete_fn(
        {"provider": "ollama", "endpoint": "http://localhost:11434", "model_name": "llava"}
    )
    with pytest.raises(RuntimeError):
        fn("p")
