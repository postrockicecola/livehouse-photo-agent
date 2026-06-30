"""Inference layer: primary abstraction for vision-language HTTP inference.

Production stacks ``PrioritizedInferenceQueue`` + :class:`InferenceRouter` /
:class:`RoundRobinInferenceRouter` over providers (see :mod:`inference.providers`).

- **Recommended:** :func:`inference.client.inference_client_from_model_config` or
  :class:`inference.client.InferenceClient` with an explicit router.
- **Legacy name:** :class:`engine.models.vlm_model.LivehouseVLM` — thin wrapper
  around the same client when ``model.use_inference_layer`` is false; prefer new imports.

YAML flag ``model.use_inference_layer`` only chooses whether the pipeline constructs
:class:`~inference.client.InferenceClient` directly or via the ``LivehouseVLM`` alias;
both paths share router/queue/provider code from this package.
"""

from inference.providers import registry as _providers_registry  # noqa: F401  # register built-in ProviderSpecs

from inference.client import InferenceClient, build_inference_router_from_model_config, inference_client_from_model_config
from inference.providers.mock import MockProvider
from inference.providers.ollama import OllamaProvider, resolve_ollama_base_urls, verify_ollama_connection
from inference.queue import InferenceModelLane
from inference.router import InferenceRouter, RoundRobinInferenceRouter

__all__ = [
    "InferenceClient",
    "InferenceModelLane",
    "InferenceRouter",
    "RoundRobinInferenceRouter",
    "OllamaProvider",
    "MockProvider",
    "build_inference_router_from_model_config",
    "inference_client_from_model_config",
    "resolve_ollama_base_urls",
    "verify_ollama_connection",
]
