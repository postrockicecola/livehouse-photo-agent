"""Executor pool naming / routing helpers."""
from __future__ import annotations

from services.pipeline_stages import STAGE_JOB_TYPE
from services.worker_pools import (
    EXECUTOR_INFERENCE,
    normalize_executor_class,
    required_executor_class_for_job,
)


def test_normalize_vlm_alias_to_inference():
    assert normalize_executor_class("vlm") == EXECUTOR_INFERENCE
    assert normalize_executor_class("VLM") == EXECUTOR_INFERENCE
    assert normalize_executor_class("gpu") == EXECUTOR_INFERENCE
    assert normalize_executor_class("inference") == EXECUTOR_INFERENCE
    assert normalize_executor_class(None) == "general"
    assert normalize_executor_class("") == "general"


def test_stage3_routes_to_inference_pool():
    req = required_executor_class_for_job(
        {
            "job_type": STAGE_JOB_TYPE,
            "stage_name": "STAGE3_VLM",
            "payload_json": None,
        }
    )
    assert req == EXECUTOR_INFERENCE
