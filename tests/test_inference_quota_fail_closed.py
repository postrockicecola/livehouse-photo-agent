"""Scope quota check failures are fail-closed unless explicitly opted open."""
from __future__ import annotations

from unittest.mock import patch

from inference.providers.mock import MockProvider
from inference.queue import PrioritizedInferenceQueue
from inference.router import InferenceRouter


def _queue() -> PrioritizedInferenceQueue:
    router = InferenceRouter(
        primary_provider=MockProvider(),
        primary_model_name="mock-vlm",
    )
    return PrioritizedInferenceQueue(
        router=router,
        num_workers=1,
        max_queue_size=4,
        batch_aggregate_window_ms=0,
    )


def test_quota_check_exception_denies_admit_by_default(monkeypatch):
    monkeypatch.delenv("LIVEHOUSE_SCOPE_QUOTA_FAIL_OPEN", raising=False)
    q = _queue()
    try:
        with patch(
            "infra.scope_quota.admit_vlm_for_scope",
            side_effect=RuntimeError("db locked forever"),
        ):
            result = q.submit_future(image_path="/tmp/x.jpg", prompt="p").result(timeout=5)
        assert result["status"] == "error"
        assert result["error"] == "scope_quota_check_failed"
        assert result["scope_quota"]["ok"] is False
    finally:
        q.shutdown(cancel_queued=True)


def test_quota_fail_open_escape_hatch(monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_SCOPE_QUOTA_FAIL_OPEN", "1")
    q = _queue()
    try:
        with patch(
            "infra.scope_quota.admit_vlm_for_scope",
            side_effect=RuntimeError("db locked forever"),
        ):
            result = q.submit_future(image_path="/tmp/x.jpg", prompt="p").result(timeout=10)
        assert result.get("error") != "scope_quota_check_failed"
        assert result.get("status") in {"ok", "success", "completed"} or "text" in result
    finally:
        q.shutdown(cancel_queued=True)
