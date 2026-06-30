"""Compatibility façade for the vision-language inference stack.

.. deprecated::
    **Imports from this module are legacy.**

    - Prefer :mod:`inference` for routing, providers, queue-backed clients, and factories.
    - Prefer :func:`inference.parsers.clean_json_response`,
      :func:`inference.parsers.parse_dimensional_response`, and
      :func:`inference.parsers.norm_bilingual_text` for Stage3 JSON handling.
    - Prefer :func:`inference.providers.ollama.resolve_ollama_base_urls` for URL resolution.

:class:`LivehouseVLM` remains the **default pipeline entrypoint name** when
``model.use_inference_layer`` is false (see ``AestheticPipeline``): it is a thin
wrapper around :class:`inference.client.InferenceClient` and does not duplicate
queue or router logic.

Each analyze call sends one image today; when a backend supports true multi-image
batching, thread ``model.inference_batch_size`` / queue batching through the inference client.
"""
from __future__ import annotations

import logging
from concurrent.futures import Future
from typing import Any, Dict, Optional

from inference.client import inference_client_from_model_config
from inference.parsers import clean_json_response, norm_bilingual_text, parse_dimensional_response
from inference.providers.ollama import resolve_ollama_base_urls, verify_ollama_connection

logger = logging.getLogger(__name__)


class LivehouseVLM:
    """
    Backward-compatible name for the production Ollama + vision model client.

    Implementation is delegated to :class:`inference.client.InferenceClient`
    (same router, queue, and provider stack as ``model.use_inference_layer: true``).
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        model_name: str = "llava",
        timeout: int = 120,
        temperature: float = 0.8,
        num_predict: int = 180,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        queue_wait_timeout_seconds: float = 60.0,
        fallback_model_name: Optional[str] = None,
        fallback_num_predict: Optional[int] = None,
        max_concurrent_requests: int = 1,
        max_inference_queue_size: int = 16,
        inference_hard_timeout_seconds: int | None = None,
        **kwargs: Any,
    ) -> None:
        if "max_concurrent_requests" in kwargs:
            max_concurrent_requests = int(kwargs.pop("max_concurrent_requests") or max_concurrent_requests)
        if "max_inference_queue_size" in kwargs:
            max_inference_queue_size = int(kwargs.pop("max_inference_queue_size") or max_inference_queue_size)
        if "inference_hard_timeout_seconds" in kwargs:
            inference_hard_timeout_seconds = kwargs.pop("inference_hard_timeout_seconds")
            if inference_hard_timeout_seconds is not None:
                inference_hard_timeout_seconds = int(inference_hard_timeout_seconds)
        ollama_endpoints_kw = kwargs.pop("ollama_endpoints", None)
        ollama_ports_kw = kwargs.pop("ollama_ports", None)
        ollama_host_kw = kwargs.pop("ollama_host", None)
        if kwargs:
            logger.debug("LivehouseVLM ignoring unused kwargs: %s", sorted(kwargs.keys()))

        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.timeout = timeout
        self.temperature = temperature
        self.num_predict = num_predict
        self.max_retries = max_retries
        self.retry_delay = retry_delay

        model_cfg: Dict[str, Any] = {
            "provider": "ollama",
            "endpoint": self.endpoint,
            "model_name": self.model_name,
            "timeout": self.timeout,
            "temperature": self.temperature,
            "num_predict": self.num_predict,
            "max_retries": self.max_retries,
            "retry_delay": self.retry_delay,
            "queue_wait_timeout_seconds": queue_wait_timeout_seconds,
            "fallback_model_name": fallback_model_name,
            "fallback_num_predict": fallback_num_predict,
            "ollama_endpoints": ollama_endpoints_kw,
            "ollama_ports": ollama_ports_kw,
            "ollama_host": ollama_host_kw,
        }
        self._ollama_urls = resolve_ollama_base_urls(model_cfg)

        self._client = inference_client_from_model_config(
            model_cfg,
            max_concurrent_requests=max_concurrent_requests,
            max_inference_queue_size=max_inference_queue_size,
            inference_hard_timeout_seconds=inference_hard_timeout_seconds,
        )

        verify_ollama_connection(self._ollama_urls, self.model_name)

    @property
    def _infer(self):  # noqa: ANN201 — compat with legacy ``self._infer`` queue handle
        return self._client._queue

    def predict(
        self,
        image_path: str,
        prompt: str,
        retry_count: int = 0,
        priority: int = 0,
        *,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        if retry_count != 0:
            logger.debug("predict(retry_count=%s) ignored; queue handles retries", retry_count)

        return self._client.predict(
            image_path,
            prompt,
            retry_count=0,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def predict_future(
        self,
        image_path: str,
        prompt: str,
        *,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Future[Dict[str, Any]]:
        return self._client.predict_future(
            image_path,
            prompt,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def infer_fast(
        self,
        image_path: str,
        prompt: str,
        *,
        priority: int = 0,
        fast_num_predict: int = 220,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self._client.infer_fast(
            image_path,
            prompt,
            priority=priority,
            fast_num_predict=fast_num_predict,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def infer_fast_future(
        self,
        image_path: str,
        prompt: str,
        *,
        priority: int = 0,
        fast_num_predict: int = 220,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Future[Dict[str, Any]]:
        return self._client.infer_fast_future(
            image_path,
            prompt,
            priority=priority,
            fast_num_predict=fast_num_predict,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def infer_full(
        self,
        image_path: str,
        prompt: str,
        *,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self._client.infer_full(
            image_path,
            prompt,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def infer_full_future(
        self,
        image_path: str,
        prompt: str,
        *,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: Dict[str, Any] | None = None,
    ) -> Future[Dict[str, Any]]:
        return self._client.infer_full_future(
            image_path,
            prompt,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            inference_extra_metadata=inference_extra_metadata,
        )

    def inference_queue_observability(self) -> Dict[str, Any]:
        return self._client.inference_queue_observability()

    @staticmethod
    def clean_json_response(raw_text: str) -> str:
        """Deprecated alias for :func:`inference.parsers.clean_json_response`."""
        return clean_json_response(raw_text)

    @staticmethod
    def parse_dimensional_response(json_str: str, raw_model_text: Optional[str] = None) -> Dict[str, Any]:
        """Deprecated alias for :func:`inference.parsers.parse_dimensional_response`."""
        return parse_dimensional_response(json_str, raw_model_text)


# -----------------------------------------------------------------------------
# Re-exports (deprecated imports — use ``inference.*`` modules instead)
# -----------------------------------------------------------------------------


def __getattr__(name: str) -> Any:
    if name == "_norm_bilingual_text":
        return norm_bilingual_text
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
