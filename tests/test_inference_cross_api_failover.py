"""Health-aware failover between independent inference API providers."""
from __future__ import annotations

import sqlite3

from inference.client import build_inference_router_from_model_config
from inference.ledger import compute_outcome_attribution
from inference.providers.base import InferenceProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse
from utils.config_loader import ConfigLoader
from utils.luma_brain import (
    _migrate_model_run_attempts_table,
    list_model_run_attempts_for_runs,
    replace_model_run_attempts,
)


class _PrimaryProvider(InferenceProvider):
    PROVIDER_ID = "cloud"

    def __init__(self, responses: list[InferenceResponse]) -> None:
        self.endpoint = "https://user:secret@cloud.example/v1?api_key=secret"
        self.responses = responses
        self.calls = 0

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        self.calls += 1
        return self.responses.pop(0)


class _FallbackProvider(InferenceProvider):
    PROVIDER_ID = "local"

    def __init__(self) -> None:
        self.endpoint = "http://localhost:11434"
        self.calls = 0

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        self.calls += 1
        return InferenceResponse(status="success", text="{}", model=model_name)


def _request() -> InferenceRequest:
    return InferenceRequest(image_path="/tmp/photo.jpg", prompt="score")


def test_retryable_transport_failure_uses_cross_api_fallback():
    primary = _PrimaryProvider(
        [InferenceResponse(status="error", error="connection timed out", model="cloud-vlm")]
    )
    fallback = _FallbackProvider()
    router = InferenceRouter(
        primary_provider=primary,
        primary_model_name="cloud-vlm",
        fallback_provider=fallback,
        fallback_model_name="local-vlm",
        fallback_retryable_only=True,
        circuit_breaker_failure_threshold=3,
    )

    response = router.infer(_request())

    assert response.status == "DEGRADED"
    assert response.is_fallback is True
    assert response.model == "local-vlm"
    ledger = response.metadata["inference_ledger"]
    assert ledger["fallback_reason"] == "primary_retryable_failure"
    assert ledger["primary_endpoint"] == "https://cloud.example/v1"
    assert ledger["fallback_endpoint"] == "http://localhost:11434"
    assert [a["provider_id"] for a in ledger["attempts"]] == ["cloud", "local"]
    assert (
        compute_outcome_attribution(ledger=ledger, payload_status=response.status)
        == "fallback_success"
    )


def test_auth_failure_is_not_masked_by_fallback():
    primary = _PrimaryProvider(
        [
            InferenceResponse(
                status="error",
                error="401 Unauthorized",
                model="cloud-vlm",
                metadata={"http_status": 401},
            )
        ]
    )
    fallback = _FallbackProvider()
    router = InferenceRouter(
        primary_provider=primary,
        primary_model_name="cloud-vlm",
        fallback_provider=fallback,
        fallback_model_name="local-vlm",
        fallback_retryable_only=True,
        circuit_breaker_failure_threshold=1,
    )

    response = router.infer(_request())

    assert response.status == "error"
    assert fallback.calls == 0
    assert response.metadata["inference_ledger"]["circuit_state"] == "closed"


def test_open_circuit_skips_primary_until_cooldown():
    primary = _PrimaryProvider(
        [
            InferenceResponse(status="error", error="connection timed out"),
            InferenceResponse(status="error", error="connection timed out"),
        ]
    )
    fallback = _FallbackProvider()
    router = InferenceRouter(
        primary_provider=primary,
        primary_model_name="cloud-vlm",
        fallback_provider=fallback,
        fallback_model_name="local-vlm",
        fallback_retryable_only=True,
        circuit_breaker_failure_threshold=2,
        circuit_breaker_recovery_seconds=60,
    )

    assert router.infer(_request()).status == "DEGRADED"
    assert router.infer(_request()).status == "DEGRADED"
    third = router.infer(_request())

    assert third.status == "DEGRADED"
    assert primary.calls == 2
    assert fallback.calls == 3
    ledger = third.metadata["inference_ledger"]
    assert ledger["fallback_reason"] == "circuit_open"
    assert ledger["attempts"][0]["primary_skipped"] is True


def test_half_open_probe_closes_circuit_after_recovery(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("inference.router.time.monotonic", lambda: clock[0])
    primary = _PrimaryProvider(
        [
            InferenceResponse(status="error", error="connection timed out"),
            InferenceResponse(status="success", text="{}", model="cloud-vlm"),
        ]
    )
    fallback = _FallbackProvider()
    router = InferenceRouter(
        primary_provider=primary,
        primary_model_name="cloud-vlm",
        fallback_provider=fallback,
        fallback_model_name="local-vlm",
        fallback_retryable_only=True,
        circuit_breaker_failure_threshold=1,
        circuit_breaker_recovery_seconds=10,
    )

    assert router.infer(_request()).status == "DEGRADED"
    clock[0] = 5.0
    assert router.infer(_request()).metadata["inference_ledger"]["circuit_state"] == "open"
    clock[0] = 11.0
    recovered = router.infer(_request())

    assert recovered.status == "success"
    assert primary.calls == 2
    assert recovered.metadata["inference_ledger"]["circuit_state"] == "closed"


def test_config_builds_openai_to_ollama_fallback():
    router = build_inference_router_from_model_config(
        {
            "provider": "openai",
            "endpoint": "https://cloud.example/v1",
            "model_name": "cloud-vlm",
            "temperature": 0.0,
            "num_predict": 512,
            "timeout": 60,
            "max_retries": 0,
            "retry_delay": 0.0,
            "fallback": {
                "enabled": True,
                "provider": "ollama",
                "endpoint": "http://localhost:11434",
                "model_name": "local-vlm",
                "max_concurrent_requests": 1,
                "circuit_breaker": {
                    "failure_threshold": 2,
                    "recovery_timeout_seconds": 30,
                },
            },
        }
    )

    assert isinstance(router, InferenceRouter)
    assert router.primary_provider.provider_id == "vllm"
    assert router.fallback_provider.provider_id == "ollama"
    assert router.fallback_model_name == "local-vlm"


def test_ollama_host_overrides_route_b_fallback_endpoint(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://host.docker.internal:11434")
    config = {
        "model": {"endpoint": "http://localhost:11434"},
        "stage3": {
            "route_b": {
                "enabled": True,
                "provider": "openai",
                "endpoint": "https://cloud.example/v1",
                "model_name": "cloud-vlm",
                "fallback": {
                    "enabled": True,
                    "provider": "ollama",
                    "endpoint": "http://localhost:11434",
                    "model_name": "local-vlm",
                },
            }
        },
    }

    resolved = ConfigLoader.get_stage3_model_config(config)

    assert resolved["fallback"]["endpoint"] == "http://host.docker.internal:11434"


def test_attempt_endpoint_and_fallback_reason_are_persisted():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE model_runs (id INTEGER PRIMARY KEY)")
    _migrate_model_run_attempts_table(conn)
    conn.execute("INSERT INTO model_runs (id) VALUES (1)")
    conn.commit()

    replace_model_run_attempts(
        conn,
        model_run_id=1,
        attempts=[
            {
                "role": "fallback",
                "provider_id": "ollama",
                "model_name": "local-vlm",
                "latency_ms": 12,
                "ok": True,
                "primary_skipped": True,
                "endpoint": "http://localhost:11434",
                "fallback_reason": "circuit_open",
            }
        ],
    )

    attempt = list_model_run_attempts_for_runs(conn, run_ids=[1])[1][0]
    assert attempt["endpoint"] == "http://localhost:11434"
    assert attempt["fallback_reason"] == "circuit_open"
