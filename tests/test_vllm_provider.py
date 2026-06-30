"""OpenAI-compatible vLLM provider: URL normalize, usage->token mapping, routing, error handling."""
from __future__ import annotations

import pytest

from inference.providers.vllm import VLLMProvider, chat_completions_url, resolve_vllm_base_urls
from inference.types import InferenceRequest


class _FakeResponse:
    def __init__(self, *, json_body=None, status_code=200, raise_exc=None):
        self._json = json_body or {}
        self.status_code = status_code
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self):
        return self._json


def test_chat_completions_url_idempotent():
    assert chat_completions_url("http://h:8000") == "http://h:8000/v1/chat/completions"
    assert chat_completions_url("http://h:8000/") == "http://h:8000/v1/chat/completions"
    assert chat_completions_url("http://h:8000/v1") == "http://h:8000/v1/chat/completions"
    assert chat_completions_url("http://h:8000/v1/chat/completions") == "http://h:8000/v1/chat/completions"


def test_resolve_base_urls_precedence():
    assert resolve_vllm_base_urls({"endpoint": "http://x:8000"}) == ["http://x:8000"]
    assert resolve_vllm_base_urls({"vllm_endpoints": ["http://a:8000", "http://b:8000"]}) == [
        "http://a:8000",
        "http://b:8000",
    ]
    assert resolve_vllm_base_urls(
        {"vllm_ports": [8000, 8001], "vllm_host": "http://127.0.0.1"}
    ) == ["http://127.0.0.1:8000", "http://127.0.0.1:8001"]
    assert resolve_vllm_base_urls({}) == ["http://localhost:8000"]


def test_parse_success_maps_usage_to_ollama_token_keys():
    res = VLLMProvider._parse_success(
        {
            "model": "qwen2-vl",
            "choices": [{"message": {"content": '{"overall": 8}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 640, "completion_tokens": 175, "total_tokens": 815},
        },
        "qwen2-vl",
    )
    assert res.status == "success"
    assert res.text == '{"overall": 8}'
    # Mapped onto the keys the model_runs token closeloop + load_test read.
    assert res.metadata["prompt_eval_count"] == 640
    assert res.metadata["eval_count"] == 175
    assert res.metadata["total_tokens"] == 815


def test_generate_success_via_mocked_http(monkeypatch):
    monkeypatch.setattr(
        "engine.operators.image_processor.ImageProcessor.get_optimized_base64",
        staticmethod(lambda *a, **k: "QkFTRTY0"),
    )
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        return _FakeResponse(
            json_body={
                "model": "llava",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }
        )

    monkeypatch.setattr("inference.providers.vllm.requests.post", fake_post)
    prov = VLLMProvider(
        endpoint="http://vllm:8000",
        temperature=0.0,
        num_predict=128,
        timeout=30,
        max_retries=0,
        retry_delay=0.0,
        api_key="secret",
    )
    res = prov.generate(InferenceRequest(image_path="/nope.jpg", prompt="rate"), model_name="llava")
    assert res.status == "success"
    assert res.metadata["eval_count"] == 4
    assert captured["url"] == "http://vllm:8000/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    content = captured["payload"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert captured["payload"]["max_tokens"] == 128


def test_generate_http_error_sets_status_code(monkeypatch):
    import requests

    monkeypatch.setattr(
        "engine.operators.image_processor.ImageProcessor.get_optimized_base64",
        staticmethod(lambda *a, **k: "x"),
    )
    err = requests.HTTPError("500 Server Error")
    err.response = _FakeResponse(status_code=500)

    def fake_post(url, json=None, headers=None, timeout=None):
        return _FakeResponse(raise_exc=err)

    monkeypatch.setattr("inference.providers.vllm.requests.post", fake_post)
    prov = VLLMProvider(
        endpoint="http://vllm:8000",
        temperature=0.0,
        num_predict=64,
        timeout=10,
        max_retries=0,
        retry_delay=0.0,
    )
    res = prov.generate(InferenceRequest(image_path="/nope.jpg", prompt="p"), model_name="m")
    assert res.status == "error"
    assert res.metadata.get("http_status") == 500


def test_client_routing_builds_vllm_provider():
    from inference.client import build_inference_router_from_model_config
    from inference.router import InferenceRouter, RoundRobinInferenceRouter

    cfg = {
        "provider": "vllm",
        "model_name": "qwen2-vl",
        "endpoint": "http://vllm:8000",
        "temperature": 0.0,
        "num_predict": 256,
        "timeout": 60,
        "max_retries": 1,
        "retry_delay": 0.5,
    }
    router = build_inference_router_from_model_config(cfg)
    assert isinstance(router, InferenceRouter)
    assert router.primary_provider.provider_id == "vllm"

    cfg_multi = {**cfg, "vllm_endpoints": ["http://a:8000", "http://b:8000"]}
    router_multi = build_inference_router_from_model_config(cfg_multi)
    assert isinstance(router_multi, RoundRobinInferenceRouter)
    assert router_multi.primary_provider.provider_id == "vllm"


def test_load_test_provider_resolution():
    import argparse

    from scripts.load_test import _build_provider, _resolve_provider_kind

    ns = argparse.Namespace(
        simulate=False,
        provider="vllm",
        endpoint="http://vllm:8000",
        api_key="",
        temperature=0.0,
        num_predict=128,
        timeout=30,
        max_retries=0,
    )
    assert _resolve_provider_kind(ns) == "vllm"
    assert _build_provider(ns).provider_id == "vllm"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
