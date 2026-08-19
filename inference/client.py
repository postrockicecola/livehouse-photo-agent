"""High-level inference client with legacy-compatible predict API."""
from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import Future
from typing import Any, Mapping

from inference.providers.base import InferenceProvider
from inference.providers.mock import MockProvider
from inference.providers.ollama import OllamaProvider, resolve_ollama_base_urls
from inference.providers.vllm import VLLMProvider, resolve_vllm_base_urls
from inference.queue import InferenceModelLane, PrioritizedInferenceQueue
from inference.router import InferenceRouter, RoundRobinInferenceRouter

logger = logging.getLogger(__name__)

RouterLike = InferenceRouter | RoundRobinInferenceRouter


def _build_provider(
    provider: str,
    config: Mapping[str, Any],
    *,
    endpoint: str | None = None,
) -> InferenceProvider:
    if provider == "mock":
        return MockProvider()
    if provider in ("vllm", "openai"):
        return VLLMProvider(
            endpoint=endpoint,
            temperature=float(config.get("temperature", 0.0)),
            num_predict=int(config.get("num_predict", 512)),
            timeout=int(config.get("timeout", 120)),
            max_retries=int(config.get("max_retries", 0)),
            retry_delay=float(config.get("retry_delay", 1.0)),
            api_key=config.get("api_key") or None,
        )
    if provider != "ollama":
        raise ValueError(f"Unsupported inference provider: {provider}")
    seed = config.get("seed")
    try:
        seed_i = int(seed) if seed is not None else None
    except (TypeError, ValueError):
        seed_i = None
    return OllamaProvider(
        endpoint=endpoint,
        temperature=float(config.get("temperature", 0.0)),
        num_predict=int(config.get("num_predict", 512)),
        timeout=int(config.get("timeout", 120)),
        max_retries=int(config.get("max_retries", 0)),
        retry_delay=float(config.get("retry_delay", 1.0)),
        json_mode=bool(config.get("json_mode", False)),
        seed=seed_i,
    )


def _cross_api_fallback(
    model_config: Mapping[str, Any],
) -> tuple[InferenceProvider, str, Mapping[str, Any]] | None:
    raw = model_config.get("fallback")
    if not isinstance(raw, Mapping) or not bool(raw.get("enabled", False)):
        return None
    provider = str(raw.get("provider") or "").strip().lower()
    endpoint = str(raw.get("endpoint") or "").strip()
    model_name = str(raw.get("model_name") or "").strip()
    if not provider or not endpoint or not model_name:
        raise ValueError(
            "Enabled model.fallback requires provider, endpoint, and model_name"
        )
    config = {**dict(model_config), **dict(raw)}
    api_key_env = str(raw.get("api_key_env") or "").strip()
    if api_key_env:
        config["api_key"] = (os.environ.get(api_key_env) or "").strip()
    return _build_provider(provider, config, endpoint=endpoint), model_name, raw


def build_inference_router_from_model_config(model_config: Mapping[str, Any]) -> RouterLike:
    """Build primary (+ optional fallback) routing from a model section dict (yaml / ``ConfigLoader``)."""
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    if provider in ("vllm", "openai"):
        urls = resolve_vllm_base_urls(model_config)
    elif provider == "ollama":
        urls = resolve_ollama_base_urls(model_config)
    elif provider == "mock":
        urls = [None]
    else:
        raise ValueError(f"Unsupported inference provider: {provider}")

    cross_fallback = _cross_api_fallback(model_config)
    legacy_fallback_model = model_config.get("fallback_model_name") or None
    routers: list[InferenceRouter] = []
    for base in urls:
        primary = _build_provider(provider, model_config, endpoint=base)
        fallback_provider = primary
        fallback_model = legacy_fallback_model
        fallback_options: Mapping[str, Any] = {}
        if cross_fallback is not None:
            fallback_provider, fallback_model, fallback_options = cross_fallback
        breaker = fallback_options.get("circuit_breaker") or {}
        if not isinstance(breaker, Mapping):
            breaker = {}
        routers.append(
            InferenceRouter(
                primary_provider=primary,
                primary_model_name=str(model_config["model_name"]),
                fallback_provider=fallback_provider,
                fallback_model_name=fallback_model,
                fallback_retryable_only=cross_fallback is not None,
                circuit_breaker_failure_threshold=int(
                    breaker.get("failure_threshold", 3)
                    if cross_fallback is not None
                    else 0
                ),
                circuit_breaker_recovery_seconds=float(
                    breaker.get("recovery_timeout_seconds", 60.0)
                ),
                fallback_max_concurrent_requests=int(
                    fallback_options.get("max_concurrent_requests", 1)
                ),
            )
        )
    if len(routers) == 1:
        return routers[0]
    logger.info("Inference round-robin across %s %s endpoints: %s", len(routers), provider, urls)
    return RoundRobinInferenceRouter(routers)


def inference_client_from_model_config(
    model_config: Mapping[str, Any],
    *,
    max_concurrent_requests: int,
    max_inference_queue_size: int,
    inference_hard_timeout_seconds: int | None = None,
) -> InferenceClient:
    """Shared constructor for pipeline code paths (legacy class name or explicit inference layer)."""
    router = build_inference_router_from_model_config(model_config)
    hard_to = inference_hard_timeout_seconds
    if hard_to is None:
        _ht = model_config.get("inference_hard_timeout_seconds")
        hard_to = None if _ht is None or _ht == "" else int(_ht)
    mq = max(1, int(max_inference_queue_size))
    _mbs = model_config.get("max_batch_size", 1)
    _baw = model_config.get("batch_aggregate_window_ms", 0)
    max_batch_size = int(_mbs) if _mbs is not None else 1
    batch_aggregate_window_ms = float(_baw or 0)
    raw_lanes = model_config.get("inference_lanes")
    inference_lanes: Mapping[str, InferenceModelLane | Mapping[str, Any]] | None = None
    if isinstance(raw_lanes, Mapping) and len(raw_lanes) > 0:
        inference_lanes = raw_lanes  # type: ignore[assignment]
    return InferenceClient(
        router=router,
        queue_wait_timeout_seconds=float(model_config.get("queue_wait_timeout_seconds", 60)),
        fallback_num_predict=model_config.get("fallback_num_predict"),
        num_workers=max(1, int(max_concurrent_requests)),
        max_retries=int(model_config.get("max_retries", 2) or 2),
        timeout=int(model_config.get("timeout", 120) or 120),
        max_queue_size=mq,
        inference_hard_timeout_seconds=hard_to,
        max_batch_size=max_batch_size,
        batch_aggregate_window_ms=batch_aggregate_window_ms,
        inference_lanes=inference_lanes,
    )


class InferenceClient:
    """Thin wrapper around router+queue with LivehouseVLM-like predict()."""

    def __init__(
        self,
        *,
        router: RouterLike,
        queue_wait_timeout_seconds: float = 60.0,
        fallback_num_predict: int | None = None,
        num_workers: int = 1,
        max_retries: int = 2,
        timeout: int = 120,
        max_queue_size: int = 16,
        inference_hard_timeout_seconds: int | None = None,
        max_batch_size: int = 1,
        batch_aggregate_window_ms: float = 0.0,
        inference_lanes: Mapping[str, InferenceModelLane | Mapping[str, Any]] | None = None,
    ) -> None:
        self._queue = PrioritizedInferenceQueue(
            router=router,
            queue_wait_timeout_seconds=queue_wait_timeout_seconds,
            fallback_num_predict=fallback_num_predict,
            num_workers=num_workers,
            max_retries=max_retries,
            timeout=timeout,
            max_queue_size=max_queue_size,
            inference_hard_timeout_seconds=inference_hard_timeout_seconds,
            max_batch_size=max_batch_size,
            batch_aggregate_window_ms=batch_aggregate_window_ms,
            inference_lanes=inference_lanes,
        )

    def inference_queue_observability(self) -> dict[str, Any]:
        return self._queue.observability_snapshot()

    def predict(
        self,
        image_path: str,
        prompt: str,
        retry_count: int = 0,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> dict:
        if retry_count:
            # Retry is handled at queue/provider level; retained for call compatibility.
            pass
        return self._queue.submit(
            image_path=image_path,
            prompt=prompt,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            metadata_extra=inference_extra_metadata,
        )

    def predict_future(
        self,
        image_path: str,
        prompt: str,
        retry_count: int = 0,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> Future[dict]:
        if retry_count:
            pass
        return self._queue.submit_future(
            image_path=image_path,
            prompt=prompt,
            priority=priority,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
            provider=provider,
            model_name=model_name,
            metadata_extra=inference_extra_metadata,
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
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> dict:
        md = dict(inference_extra_metadata or {})
        md["num_predict"] = int(md.get("num_predict") or fast_num_predict)
        return self.predict(
            image_path,
            prompt,
            0,
            priority,
            trace_id,
            job_id,
            session_id,
            photo_id,
            worker_id,
            provider,
            model_name,
            md,
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
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> Future[dict]:
        md = dict(inference_extra_metadata or {})
        md["num_predict"] = int(md.get("num_predict") or fast_num_predict)
        return self.predict_future(
            image_path,
            prompt,
            0,
            priority,
            trace_id,
            job_id,
            session_id,
            photo_id,
            worker_id,
            provider,
            model_name,
            md,
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
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> dict:
        return self.predict(
            image_path,
            prompt,
            0,
            priority,
            trace_id,
            job_id,
            session_id,
            photo_id,
            worker_id,
            provider,
            model_name,
            inference_extra_metadata,
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
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> Future[dict]:
        return self.predict_future(
            image_path,
            prompt,
            0,
            priority,
            trace_id,
            job_id,
            session_id,
            photo_id,
            worker_id,
            provider,
            model_name,
            inference_extra_metadata,
        )

    async def predict_async(
        self,
        image_path: str,
        prompt: str,
        retry_count: int = 0,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        inference_extra_metadata: dict[str, Any] | None = None,
    ) -> dict:
        """Same as :meth:`predict` but yields the event loop while waiting on backpressure / inference."""
        return await asyncio.to_thread(
            self.predict,
            image_path,
            prompt,
            retry_count,
            priority,
            trace_id,
            job_id,
            session_id,
            photo_id,
            worker_id,
            provider,
            model_name,
            inference_extra_metadata,
        )
