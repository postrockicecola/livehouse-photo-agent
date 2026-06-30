"""Provider interface for inference backends."""
from __future__ import annotations

from abc import ABC, abstractmethod

from inference.types import InferenceRequest, InferenceResponse


class InferenceProvider(ABC):
    """Set ``PROVIDER_ID`` on each subclass (stable slug for metrics and registry)."""

    PROVIDER_ID: str

    @property
    def provider_id(self) -> str:
        pid = getattr(type(self), "PROVIDER_ID", None)
        if not isinstance(pid, str) or not pid.strip():
            raise TypeError(f"{type(self).__name__} must define non-empty PROVIDER_ID: str")
        return pid.strip().lower()

    def supports_batch(self) -> bool:
        """True when :meth:`generate_batch` is implemented for this backend."""
        return False

    def generate_batch(self, requests: list[InferenceRequest], *, model_name: str) -> list[InferenceResponse]:
        """Run multiple requests in one provider-native batch (optional)."""
        raise NotImplementedError

    @abstractmethod
    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        """Run one multimodal generation request."""
