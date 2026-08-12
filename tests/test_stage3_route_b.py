from __future__ import annotations

from inference.providers.registry import get_provider_spec
from services.processor.pipeline_image_ops import assess_stage1_opencv
from services.processor.stages.deep_analysis import (
    stage3_inference_request_metadata,
)
from utils.config_loader import ConfigLoader


def test_route_b_uses_dedicated_cloud_provider_and_env_key(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    config = {
        "model": {
            "provider": "ollama",
            "endpoint": "http://localhost:11434",
            "model_name": "qwen2.5vl:7b",
            "temperature": 0.8,
        },
        "stage3": {
            "route_b": {
                "enabled": True,
                "provider": "openai",
                "endpoint": "https://dashscope.example/v1",
                "model_name": "qwen3-vl-plus",
                "api_key_env": "DASHSCOPE_API_KEY",
                "temperature": 0.0,
            }
        },
    }
    resolved = ConfigLoader.get_stage3_model_config(config)
    assert resolved["provider"] == "openai"
    assert resolved["model_name"] == "qwen3-vl-plus"
    assert resolved["api_key"] == "sk-test"
    assert resolved["temperature"] == 0.0
    assert resolved["use_inference_layer"] is True
    assert config["model"]["provider"] == "ollama"


def test_route_b_forces_json_mode_and_high_resolution() -> None:
    config = {
        "stage3": {
            "route_b": {
                "enabled": True,
                "thumbnail_max_side": 1600,
            }
        }
    }
    metadata = stage3_inference_request_metadata(
        config,
        {"trace_id": "trace-1", "json_mode": False},
    )
    assert metadata["trace_id"] == "trace-1"
    assert metadata["json_mode"] is True
    assert metadata["vlm_thumbnail_max_side"] == 1600


def test_openai_cloud_provider_is_discoverable() -> None:
    spec = get_provider_spec("openai")
    assert spec is not None
    assert spec.supports_remote_endpoint is True


def test_eval_force_stage3_preserves_rejection_evidence_but_admits(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.processor.pipeline_image_ops.ImageProcessor.assess_image_quality",
        lambda *_args, **_kwargs: (
            False,
            "Severe blur without structure",
            1.5,
            {"reject": "Severe blur without structure"},
        ),
    )

    passed, reason, score, debug = assess_stage1_opencv(
        {"processing": {"eval_force_stage3": True}},
        "bad.jpg",
    )

    assert passed is True
    assert reason == ""
    assert score == 1.5
    assert debug["stage1_eval_original_passed"] is False
    assert debug["stage1_eval_original_reason"] == "Severe blur without structure"
    assert debug["stage1_eval_force_stage3"] is True
