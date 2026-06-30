"""High-level inference client with legacy-compatible predict API."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import Any, Mapping

from inference.providers.mock import MockProvider
from inference.providers.ollama import OllamaProvider, resolve_ollama_base_urls
from inference.providers.vllm import VLLMProvider, resolve_vllm_base_urls
from inference.queue import InferenceModelLane, PrioritizedInferenceQueue
from inference.router import InferenceRouter, RoundRobinInferenceRouter

logger = logging.getLogger(__name__)

RouterLike = InferenceRouter | RoundRobinInferenceRouter


def build_inference_router_from_model_config(model_config: Mapping[str, Any]) -> RouterLike:
    """Build primary (+ optional fallback) routing from a model section dict (yaml / ``ConfigLoader``)."""
    provider = str(model_config.get("provider", "ollama") or "ollama").strip().lower()
    if provider == "mock":
        primary_provider = MockProvider()
        return InferenceRouter(
            primary_provider=primary_provider,
            primary_model_name=str(model_config.get("model_name", "mock-vlm")),
        )

    if provider in ("vllm", "openai"):
        urls = resolve_vllm_base_urls(model_config)
        fb_model = model_config.get("fallback_model_name") or None
        api_key = model_config.get("api_key") or None
        routers_v: list[InferenceRouter] = []
        for base in urls:
            vp = VLLMProvider(
                endpoint=base,
                temperature=float(model_config["temperature"]),
                num_predict=int(model_config["num_predict"]),
                timeout=int(model_config["timeout"]),
                max_retries=int(model_config["max_retries"]),
                retry_delay=float(model_config["retry_delay"]),
                api_key=api_key,
            )
            routers_v.append(
                InferenceRouter(
                    primary_provider=vp,
                    primary_model_name=str(model_config["model_name"]),
                    fallback_provider=vp,
                    fallback_model_name=fb_model,
                )
            )
        if len(routers_v) == 1:
            return routers_v[0]
        logger.info("Inference round-robin across %s vLLM endpoints: %s", len(routers_v), urls)
        return RoundRobinInferenceRouter(routers_v)

    urls = resolve_ollama_base_urls(model_config)
    fb_model = model_config.get("fallback_model_name") or None
    routers_o: list[InferenceRouter] = []
    for base in urls:
        opp = OllamaProvider(
            endpoint=base,
            temperature=float(model_config["temperature"]),
            num_predict=int(model_config["num_predict"]),
            timeout=int(model_config["timeout"]),
            max_retries=int(model_config["max_retries"]),
            retry_delay=float(model_config["retry_delay"]),
        )
        routers_o.append(
            InferenceRouter(
                primary_provider=opp,
                primary_model_name=str(model_config["model_name"]),
                fallback_provider=opp,
                fallback_model_name=fb_model,
            )
        )
    if len(routers_o) == 1:
        return routers_o[0]
    logger.info("Inference round-robin across %s Ollama endpoints: %s", len(routers_o), urls)
    return RoundRobinInferenceRouter(routers_o)


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
