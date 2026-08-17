"""Ollama honors json_mode / seed without calling a live server."""
from __future__ import annotations

from inference.providers.ollama import OllamaProvider
from inference.types import InferenceRequest


class _FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"response": '{"ok": true}', "eval_count": 3, "prompt_eval_count": 9}


def test_ollama_payload_sets_format_json_and_seed(monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.operators.image_processor.ImageProcessor.get_optimized_base64",
        staticmethod(lambda *a, **k: "QkFTRTY0"),
    )
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr("inference.providers.ollama.requests.post", fake_post)
    provider = OllamaProvider(
        endpoint="http://127.0.0.1:11434",
        temperature=0.0,
        num_predict=64,
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
    )
    result = provider.generate(
        InferenceRequest(
            image_path="/nope.jpg",
            prompt="json only",
            metadata={"json_mode": True, "seed": 20260817},
        ),
        model_name="qwen2.5vl:7b",
    )
    assert result.status == "success"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["options"]["seed"] == 20260817
    assert captured["payload"]["options"]["temperature"] == 0.0


def test_ollama_constructor_json_mode_default(monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.operators.image_processor.ImageProcessor.get_optimized_base64",
        staticmethod(lambda *a, **k: "x"),
    )
    captured: dict = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr("inference.providers.ollama.requests.post", fake_post)
    provider = OllamaProvider(
        endpoint="http://127.0.0.1:11434",
        temperature=0.2,
        num_predict=32,
        timeout=5,
        max_retries=0,
        retry_delay=0.0,
        json_mode=True,
        seed=7,
    )
    provider.generate(InferenceRequest(image_path="/nope.jpg", prompt="p"), model_name="m")
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["options"]["seed"] == 7
