"""Inference router: provider/model selection + fallback policy."""
from __future__ import annotations

import threading
import time
from typing import Any

from inference.ledger import (
    append_inference_attempt,
    build_empty_ledger,
    classify_inference_error,
)
from inference.providers.base import InferenceProvider
from inference.types import InferenceRequest, InferenceResponse, inference_status_ok


def _infer_ok(status: str) -> bool:
    return inference_status_ok(status)


class InferenceRouter:
    def __init__(
        self,
        *,
        primary_provider: InferenceProvider,
        primary_model_name: str,
        fallback_provider: InferenceProvider | None = None,
        fallback_model_name: str | None = None,
    ) -> None:
        self.primary_provider = primary_provider
        self.primary_model_name = primary_model_name
        self.fallback_provider = fallback_provider or primary_provider
        self.fallback_model_name = fallback_model_name

    def _generate_with_metrics(
        self,
        provider: InferenceProvider,
        request: InferenceRequest,
        *,
        model_name: str,
    ) -> tuple[InferenceResponse, int]:
        from infra.metrics import (
            record_inference_latency,
            record_provider_failure,
            record_provider_request,
        )

        pid = provider.provider_id
        record_provider_request(pid)
        t0 = time.perf_counter()
        res = provider.generate(request, model_name=model_name)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        res.provider_hop_ms = latency_ms
        record_inference_latency(pid, latency_ms)
        if not _infer_ok(res.status):
            record_provider_failure(pid)
        if res.error and not res.error_type:
            res.error_type = classify_inference_error(
                error_message=res.error,
                status_code=(res.metadata or {}).get("http_status")
                if isinstance(res.metadata, dict)
                else None,
            )
        return res, latency_ms

    def _merge_ledger(self, res: InferenceResponse, ledger: dict[str, Any]) -> InferenceResponse:
        meta = dict(res.metadata or {})
        existing = dict(meta.get("inference_ledger") or {})
        existing.update(ledger)
        meta["inference_ledger"] = existing
        res.metadata = meta
        return res

    def infer(
        self,
        request: InferenceRequest,
        *,
        force_fallback: bool = False,
        fallback_num_predict: int | None = None,
    ) -> InferenceResponse:
        from infra.metrics import record_provider_fallback

        t_wall0 = time.perf_counter()
        ledger = build_empty_ledger()
        p_prov = self.primary_provider.provider_id
        f_prov = self.fallback_provider.provider_id
        f_model = self.fallback_model_name
        p_model = request.model_name or self.primary_model_name
        ledger["primary_provider"] = p_prov
        ledger["fallback_provider"] = f_prov
        ledger["primary_model"] = p_model

        if force_fallback and f_model:
            fb_req = InferenceRequest(
                image_path=request.image_path,
                prompt=request.prompt,
                priority=request.priority,
                model_name=f_model,
                metadata=dict(request.metadata),
            )
            if fallback_num_predict is not None:
                fb_req.metadata["num_predict"] = int(fallback_num_predict)
            record_provider_fallback(self.primary_provider.provider_id)
            res, hop = self._generate_with_metrics(
                self.fallback_provider,
                fb_req,
                model_name=f_model,
            )
            append_inference_attempt(
                ledger,
                role="fallback",
                provider_id=self.fallback_provider.provider_id,
                model_name=f_model,
                latency_ms=hop,
                ok=_infer_ok(res.status),
                error_type=(res.error_type if not _infer_ok(res.status) else None),
                error_message=res.error if not _infer_ok(res.status) else None,
                primary_skipped=True,
            )
            e2e_ms = int((time.perf_counter() - t_wall0) * 1000)
            ledger["router_fallback_used"] = True
            ledger["queue_wait_degraded"] = True
            ledger["primary_latency_ms"] = 0
            ledger["fallback_hop_latency_ms"] = hop
            ledger["provider_latency_ms"] = hop
            ledger["end_to_end_latency_ms"] = e2e_ms
            if res.model:
                ledger["final_model"] = res.model
            else:
                ledger["final_model"] = f_model
            if _infer_ok(res.status):
                res.status = "DEGRADED"
                res.is_fallback = True
            if not _infer_ok(res.status) and not res.error_type:
                res.error_type = classify_inference_error(
                    error_message=res.error,
                    status_code=(res.metadata or {}).get("http_status")
                    if isinstance(res.metadata, dict)
                    else None,
                )
            if res.error_type:
                ledger["error_type"] = res.error_type
            return self._merge_ledger(res, ledger)

        res, primary_ms = self._generate_with_metrics(
            self.primary_provider,
            request,
            model_name=p_model,
        )
        if _infer_ok(res.status):
            append_inference_attempt(
                ledger,
                role="primary",
                provider_id=self.primary_provider.provider_id,
                model_name=p_model,
                latency_ms=primary_ms,
                ok=True,
            )
            e2e_ms = int((time.perf_counter() - t_wall0) * 1000)
            ledger["primary_latency_ms"] = primary_ms
            ledger["provider_latency_ms"] = primary_ms
            ledger["end_to_end_latency_ms"] = e2e_ms
            ledger["final_model"] = res.model or p_model
            if res.error_type:
                ledger["error_type"] = res.error_type
            return self._merge_ledger(res, ledger)

        if f_model:
            append_inference_attempt(
                ledger,
                role="primary",
                provider_id=self.primary_provider.provider_id,
                model_name=p_model,
                latency_ms=primary_ms,
                ok=False,
                error_type=res.error_type,
                error_message=res.error,
            )
            fb_req = InferenceRequest(
                image_path=request.image_path,
                prompt=request.prompt,
                priority=request.priority,
                model_name=f_model,
                metadata=dict(request.metadata),
            )
            if fallback_num_predict is not None:
                fb_req.metadata["num_predict"] = int(fallback_num_predict)
            record_provider_fallback(self.primary_provider.provider_id)
            res_fb, fb_ms = self._generate_with_metrics(
                self.fallback_provider,
                fb_req,
                model_name=f_model,
            )
            append_inference_attempt(
                ledger,
                role="fallback",
                provider_id=self.fallback_provider.provider_id,
                model_name=f_model,
                latency_ms=fb_ms,
                ok=_infer_ok(res_fb.status),
                error_type=(res_fb.error_type if not _infer_ok(res_fb.status) else None),
                error_message=res_fb.error if not _infer_ok(res_fb.status) else None,
            )
            e2e_ms = int((time.perf_counter() - t_wall0) * 1000)
            ledger["router_fallback_used"] = True
            ledger["primary_latency_ms"] = primary_ms
            ledger["fallback_hop_latency_ms"] = fb_ms
            ledger["provider_latency_ms"] = int(primary_ms) + int(fb_ms)
            ledger["end_to_end_latency_ms"] = e2e_ms
            ledger["final_model"] = res_fb.model or f_model
            if _infer_ok(res_fb.status):
                res_fb.status = "DEGRADED"
                res_fb.is_fallback = True
            if not _infer_ok(res_fb.status) and not res_fb.error_type:
                res_fb.error_type = classify_inference_error(
                    error_message=res_fb.error,
                    status_code=(res_fb.metadata or {}).get("http_status")
                    if isinstance(res_fb.metadata, dict)
                    else None,
                )
            if res_fb.error_type:
                ledger["error_type"] = res_fb.error_type
            return self._merge_ledger(res_fb, ledger)

        append_inference_attempt(
            ledger,
            role="primary",
            provider_id=self.primary_provider.provider_id,
            model_name=p_model,
            latency_ms=primary_ms,
            ok=False,
            error_type=res.error_type,
            error_message=res.error,
        )
        e2e_ms = int((time.perf_counter() - t_wall0) * 1000)
        ledger["primary_latency_ms"] = primary_ms
        ledger["provider_latency_ms"] = primary_ms
        ledger["end_to_end_latency_ms"] = e2e_ms
        ledger["final_model"] = res.model or p_model
        if not res.error_type:
            res.error_type = classify_inference_error(
                error_message=res.error,
                status_code=(res.metadata or {}).get("http_status")
                if isinstance(res.metadata, dict)
                else None,
            )
        if res.error_type:
            ledger["error_type"] = res.error_type
        return self._merge_ledger(res, ledger)

    def supports_batch_inference(self) -> bool:
        """True when the primary provider exposes a native :meth:`generate_batch` path."""
        return bool(self.primary_provider.supports_batch())

    def infer_batch(
        self,
        requests: list[InferenceRequest],
        *,
        force_fallback_flags: list[bool],
        fallback_num_predict: int | None = None,
    ) -> list[InferenceResponse]:
        """Run compatible requests together when batching is supported; otherwise per-item :meth:`infer`."""
        if not requests:
            return []
        if len(requests) != len(force_fallback_flags):
            raise ValueError("force_fallback_flags length must match requests")
        if len(requests) == 1:
            return [
                self.infer(
                    requests[0],
                    force_fallback=bool(force_fallback_flags[0]),
                    fallback_num_predict=fallback_num_predict,
                )
            ]
        if any(force_fallback_flags) or not self.supports_batch_inference():
            return [
                self.infer(
                    r,
                    force_fallback=bool(fb),
                    fallback_num_predict=fallback_num_predict,
                )
                for r, fb in zip(requests, force_fallback_flags)
            ]

        from infra.metrics import (
            record_inference_latency,
            record_provider_request,
        )

        p_model = requests[0].model_name or self.primary_model_name
        p_prov = self.primary_provider.provider_id
        f_prov = self.fallback_provider.provider_id
        p0 = requests[0].prompt
        n0 = requests[0].metadata.get("num_predict", "__npdef__")
        for r in requests[1:]:
            if (r.model_name or self.primary_model_name) != p_model or r.prompt != p0:
                return [
                    self.infer(
                        r2,
                        force_fallback=False,
                        fallback_num_predict=fallback_num_predict,
                    )
                    for r2 in requests
                ]
            if r.metadata.get("num_predict", "__npdef__") != n0:
                return [
                    self.infer(
                        r2,
                        force_fallback=False,
                        fallback_num_predict=fallback_num_predict,
                    )
                    for r2 in requests
                ]

        for _ in requests:
            record_provider_request(p_prov)
        t_wall0 = time.perf_counter()
        try:
            results = self.primary_provider.generate_batch(requests, model_name=p_model)
        except Exception:
            return [
                self.infer(
                    r,
                    force_fallback=False,
                    fallback_num_predict=fallback_num_predict,
                )
                for r in requests
            ]
        if len(results) != len(requests):
            return [
                self.infer(
                    r,
                    force_fallback=False,
                    fallback_num_predict=fallback_num_predict,
                )
                for r in requests
            ]

        if any(not _infer_ok(r.status) for r in results):
            return [
                self.infer(
                    r,
                    force_fallback=False,
                    fallback_num_predict=fallback_num_predict,
                )
                for r in requests
            ]

        batch_ms = int((time.perf_counter() - t_wall0) * 1000)
        record_inference_latency(p_prov, batch_ms)
        out: list[InferenceResponse] = []
        for res in results:
            if res.error and not res.error_type:
                res.error_type = classify_inference_error(
                    error_message=res.error,
                    status_code=(res.metadata or {}).get("http_status")
                    if isinstance(res.metadata, dict)
                    else None,
                )
            ledger = build_empty_ledger()
            ledger["primary_provider"] = p_prov
            ledger["fallback_provider"] = f_prov
            ledger["primary_model"] = p_model
            append_inference_attempt(
                ledger,
                role="primary",
                provider_id=p_prov,
                model_name=p_model,
                latency_ms=batch_ms,
                ok=_infer_ok(res.status),
                error_type=(res.error_type if not _infer_ok(res.status) else None),
                error_message=res.error if not _infer_ok(res.status) else None,
            )
            e2e_ms = int((time.perf_counter() - t_wall0) * 1000)
            ledger["primary_latency_ms"] = batch_ms
            ledger["provider_latency_ms"] = batch_ms
            ledger["end_to_end_latency_ms"] = e2e_ms
            ledger["final_model"] = res.model or p_model
            if res.error_type:
                ledger["error_type"] = res.error_type
            out.append(self._merge_ledger(res, ledger))
        return out


class RoundRobinInferenceRouter:
    """
    Thread-safe round-robin across multiple single-provider InferenceRouter instances.

    Each ``infer()`` call uses the next router so concurrent workers spread across Ollama instances.
    """

    def __init__(self, routers: list[InferenceRouter]) -> None:
        if not routers:
            raise ValueError("routers must be non-empty")
        self._routers = routers
        self._lock = threading.Lock()
        self._idx = 0

    def infer(
        self,
        request: InferenceRequest,
        *,
        force_fallback: bool = False,
        fallback_num_predict: int | None = None,
    ) -> InferenceResponse:
        with self._lock:
            r = self._routers[self._idx % len(self._routers)]
            self._idx += 1
        return r.infer(
            request,
            force_fallback=force_fallback,
            fallback_num_predict=fallback_num_predict,
        )

    def supports_batch_inference(self) -> bool:
        return bool(self._routers[0].supports_batch_inference())

    def infer_batch(
        self,
        requests: list[InferenceRequest],
        *,
        force_fallback_flags: list[bool],
        fallback_num_predict: int | None = None,
    ) -> list[InferenceResponse]:
        with self._lock:
            r = self._routers[self._idx % len(self._routers)]
            self._idx += 1
        return r.infer_batch(
            requests,
            force_fallback_flags=force_fallback_flags,
            fallback_num_predict=fallback_num_predict,
        )

    @property
    def primary_provider(self) -> InferenceProvider:
        return self._routers[0].primary_provider

    @property
    def fallback_provider(self) -> InferenceProvider:
        return self._routers[0].fallback_provider

    @property
    def primary_model_name(self) -> str:
        return self._routers[0].primary_model_name

    @property
    def fallback_model_name(self) -> str | None:
        return self._routers[0].fallback_model_name
