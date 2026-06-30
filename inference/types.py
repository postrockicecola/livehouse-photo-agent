"""Shared inference request/response types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def inference_status_ok(status: str | None) -> bool:
    """True when inference returned a usable body (primary success or degraded/fallback success)."""
    s = (status or "").strip().lower()
    return s in ("success", "degraded")


@dataclass(slots=True)
class InferenceRequest:
    image_path: str
    prompt: str
    priority: int = 0
    model_name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class InferenceResponse:
    status: str
    text: str = ""
    model: str = ""
    error: str | None = None
    # Optional: single-hop wall time for the last :meth:`InferenceProvider.generate` (ms).
    provider_hop_ms: int | None = None
    # Coarse :mod:`inference.ledger` label when ``status != success`` (or forced on success if set).
    error_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # True when a fallback / degraded path produced a usable body (``status`` is ``DEGRADED``).
    is_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "text": self.text,
            "model": self.model,
            "metadata": self.metadata,
        }
        if self.error:
            payload["error"] = self.error
        if self.provider_hop_ms is not None:
            payload["provider_hop_ms"] = self.provider_hop_ms
        if self.error_type is not None:
            payload["error_type"] = self.error_type
        if self.is_fallback:
            payload["is_fallback"] = True
        return payload
