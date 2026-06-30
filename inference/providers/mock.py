"""Mock provider for tests/dev."""
from __future__ import annotations

from inference.providers.base import InferenceProvider
from inference.types import InferenceRequest, InferenceResponse


class MockProvider(InferenceProvider):
    PROVIDER_ID = "mock"

    def __init__(self, *, fixed_text: str = '{"score": 5}', model_name: str = "mock-vlm") -> None:
        self.fixed_text = fixed_text
        self.default_model_name = model_name

    def supports_batch(self) -> bool:
        return True

    def generate_batch(self, requests: list[InferenceRequest], *, model_name: str) -> list[InferenceResponse]:
        return [self.generate(r, model_name=model_name) for r in requests]

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        return InferenceResponse(
            status="success",
            text=self.fixed_text,
            model=model_name or self.default_model_name,
            metadata={"mock": True, "priority": request.priority},
        )
