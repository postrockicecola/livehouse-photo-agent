"""Single-flight Ollama VLM inference with in-memory priority queue and queue-wait degradation."""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import requests

from utils.logging_context import make_log_extra
from inference.providers.ollama import OllamaProvider
from infra.metrics import (
    record_inference_latency,
    record_provider_failure,
    record_provider_fallback,
    record_provider_request,
)

_METRICS_PROVIDER = OllamaProvider.PROVIDER_ID

logger = logging.getLogger(__name__)


@dataclass
class _InferenceJob:
    image_path: str
    prompt: str
    trace_id: str | None = None
    job_id: int | None = None
    session_id: int | None = None
    photo_id: int | None = None
    worker_id: int | None = None
    enqueued_mono: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    result: Optional[Dict[str, Any]] = None


class PrioritizedInferenceQueue:
    """
    VLM /api/generate calls share a priority queue and ``num_workers`` parallel HTTP workers.
    Submitters block until their job completes. Lower ``priority`` is scheduled first.
    If queue wait exceeds ``queue_wait_timeout_seconds`` before a worker *starts* the job,
    use ``fallback_model_name`` / ``fallback_num_predict`` or return error.
    Set OLLAMA_NUM_PARALLEL >= num_workers on the Ollama host for best throughput.
    """

    def __init__(
        self,
        endpoint: str,
        model_name: str,
        temperature: float,
        num_predict: int,
        timeout: int,
        max_retries: int,
        retry_delay: float,
        queue_wait_timeout_seconds: float = 60.0,
        fallback_model_name: Optional[str] = None,
        fallback_num_predict: Optional[int] = None,
        num_workers: int = 1,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.model_name = model_name
        self.temperature = temperature
        self.num_predict = num_predict
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.queue_wait_timeout_seconds = float(queue_wait_timeout_seconds)
        self.fallback_model_name = (fallback_model_name or "").strip() or None
        self.fallback_num_predict = fallback_num_predict

        self._pq = queue.PriorityQueue()
        self._seq = itertools.count()
        self._stop = threading.Event()
        nw = max(1, int(num_workers))
        self._workers: list[threading.Thread] = []
        for i in range(nw):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"vlm-inference-queue-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        logger.info("VLM inference queue started with %s parallel worker(s)", nw)

    def shutdown(self) -> None:
        self._stop.set()

    def _client_wait_cap(self) -> float:
        """Upper bound for submitter blocking (queue + HTTP retries)."""
        attempts = self.max_retries + 1
        return max(900.0, self.queue_wait_timeout_seconds + 120.0 + self.timeout * attempts * 2)

    def submit(
        self,
        image_path: str,
        prompt: str,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
    ) -> Dict[str, Any]:
        job = _InferenceJob(
            image_path=image_path,
            prompt=prompt,
            trace_id=trace_id,
            job_id=job_id,
            session_id=session_id,
            photo_id=photo_id,
            worker_id=worker_id,
        )
        self._pq.put((priority, next(self._seq), job))
        logger.info(
            "inference queued",
            extra=make_log_extra(
                trace_id=trace_id,
                job_id=job_id,
                session_id=session_id,
                photo_id=photo_id,
                worker_id=worker_id,
                provider=_METRICS_PROVIDER,
                model=self.model_name,
                status="QUEUED",
            ),
        )
        cap = self._client_wait_cap()
        if not job.done.wait(timeout=cap):
            logger.error(
                "VLM queue client wait exceeded %.0fs for %s",
                cap,
                image_path,
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    session_id=session_id,
                    photo_id=photo_id,
                    worker_id=worker_id,
                    provider=_METRICS_PROVIDER,
                    model=self.model_name,
                    status="FAILED",
                    latency_ms=int(cap * 1000),
                    error_code="QUEUE_WAIT_EXCEEDED",
                ),
            )
            return {
                "status": "error",
                "error": f"VLM inference queue client wait exceeded {cap:.0f}s",
                "text": "",
                "model": self.model_name,
            }
        if job.result is None:
            return {
                "status": "error",
                "error": "VLM inference queue returned no result",
                "text": "",
                "model": self.model_name,
            }
        return job.result

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _pri, _seq, job = self._pq.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                job.result = self._run_job(job)
            except Exception as e:
                logger.exception(
                    "VLM queue worker error: %s",
                    e,
                    extra=make_log_extra(
                        trace_id=job.trace_id,
                        job_id=job.job_id,
                        session_id=job.session_id,
                        photo_id=job.photo_id,
                        worker_id=job.worker_id,
                        provider=_METRICS_PROVIDER,
                        model=self.model_name,
                        status="FAILED",
                        error_code=type(e).__name__,
                    ),
                )
                job.result = {
                    "status": "error",
                    "error": str(e),
                    "text": "",
                    "model": self.model_name,
                }
            finally:
                job.done.set()

    def _run_job(self, job: _InferenceJob) -> Dict[str, Any]:
        from engine.operators.image_processor import ImageProcessor

        wait = time.monotonic() - job.enqueued_mono
        model = self.model_name
        num_predict = self.num_predict
        degraded = False

        if wait > self.queue_wait_timeout_seconds:
            if self.fallback_model_name:
                model = self.fallback_model_name
                degraded = True
                if self.fallback_num_predict is not None:
                    num_predict = int(self.fallback_num_predict)
                else:
                    num_predict = min(num_predict, 256)
                logger.warning(
                    "VLM queue wait %.1fs > %.0fs → degraded inference: model=%s num_predict=%s (%s)",
                    wait,
                    self.queue_wait_timeout_seconds,
                    model,
                    num_predict,
                    job.image_path,
                )
                logger.warning(
                    "inference fallback",
                    extra=make_log_extra(
                        trace_id=job.trace_id,
                        job_id=job.job_id,
                        session_id=job.session_id,
                        photo_id=job.photo_id,
                        worker_id=job.worker_id,
                        provider=_METRICS_PROVIDER,
                        model=model,
                        status="FALLBACK",
                    ),
                )
                record_provider_fallback(_METRICS_PROVIDER)
            else:
                msg = (
                    f"Queue wait {wait:.1f}s exceeded limit {self.queue_wait_timeout_seconds:.0f}s "
                    f"(no fallback_model_name configured)"
                )
                logger.warning("%s | %s", msg, job.image_path)
                return {
                    "status": "error",
                    "error": msg,
                    "text": "",
                    "model": self.model_name,
                    "metadata": {"queue_wait_sec": wait, "degraded": False},
                }

        img_base64 = ImageProcessor.get_optimized_base64(job.image_path)
        url = f"{self.endpoint}/api/generate"
        connect_t = min(60, max(10, self.timeout // 3))
        req_timeout = (connect_t, self.timeout)

        attempts = self.max_retries + 1
        infer_t0 = time.perf_counter()
        logger.info(
            "inference request start",
            extra=make_log_extra(
                trace_id=job.trace_id,
                job_id=job.job_id,
                session_id=job.session_id,
                photo_id=job.photo_id,
                worker_id=job.worker_id,
                provider=_METRICS_PROVIDER,
                model=model,
                status="INFERENCING",
            ),
        )
        for attempt in range(attempts):
            record_provider_request(_METRICS_PROVIDER)
            payload = {
                "model": model,
                "prompt": job.prompt,
                "images": [img_base64],
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": num_predict,
                },
            }
            try:
                response = requests.post(url, json=payload, timeout=req_timeout)
                response.raise_for_status()
                result = response.json()
                logger.info(
                    "inference succeeded",
                    extra=make_log_extra(
                        trace_id=job.trace_id,
                        job_id=job.job_id,
                        session_id=job.session_id,
                        photo_id=job.photo_id,
                        worker_id=job.worker_id,
                        provider=_METRICS_PROVIDER,
                        model=model,
                        status=("DEGRADED" if degraded else "SUCCEEDED"),
                        latency_ms=int((time.perf_counter() - infer_t0) * 1000),
                    ),
                )
                record_inference_latency(_METRICS_PROVIDER, int((time.perf_counter() - infer_t0) * 1000))
                return {
                    "status": ("DEGRADED" if degraded else "success"),
                    "text": result.get("response", "").strip(),
                    "model": model,
                    "is_fallback": bool(degraded),
                    "metadata": {
                        "eval_count": result.get("eval_count"),
                        "prompt_eval_count": result.get("prompt_eval_count"),
                        "queue_wait_sec": wait,
                        "degraded": degraded,
                    },
                }
            except (requests.Timeout, requests.ConnectionError) as e:
                if attempt + 1 < attempts:
                    logger.warning(
                        "API timeout/connection error (%s/%s), retrying…",
                        attempt + 1,
                        self.max_retries,
                    )
                    logger.warning(
                        "inference retry",
                        extra=make_log_extra(
                            trace_id=job.trace_id,
                            job_id=job.job_id,
                            session_id=job.session_id,
                            photo_id=job.photo_id,
                            worker_id=job.worker_id,
                            provider=_METRICS_PROVIDER,
                            model=model,
                            status="RETRYING",
                            error_code=type(e).__name__,
                        ),
                    )
                    time.sleep(self.retry_delay)
                    continue
                logger.error("API error after %s retries: %s", self.max_retries, e)
                record_provider_failure(_METRICS_PROVIDER)
                record_inference_latency(_METRICS_PROVIDER, int((time.perf_counter() - infer_t0) * 1000))
                logger.error(
                    "inference failed",
                    extra=make_log_extra(
                        trace_id=job.trace_id,
                        job_id=job.job_id,
                        session_id=job.session_id,
                        photo_id=job.photo_id,
                        worker_id=job.worker_id,
                        provider=_METRICS_PROVIDER,
                        model=model,
                        status="FAILED",
                        latency_ms=int((time.perf_counter() - infer_t0) * 1000),
                        error_code=type(e).__name__,
                    ),
                )
                return {
                    "status": "error",
                    "error": str(e),
                    "text": "",
                    "model": model,
                    "metadata": {"queue_wait_sec": wait, "degraded": degraded},
                }
            except Exception as e:
                logger.error("Unexpected error in VLM HTTP: %s", e)
                record_provider_failure(_METRICS_PROVIDER)
                record_inference_latency(_METRICS_PROVIDER, int((time.perf_counter() - infer_t0) * 1000))
                logger.error(
                    "inference failed",
                    extra=make_log_extra(
                        trace_id=job.trace_id,
                        job_id=job.job_id,
                        session_id=job.session_id,
                        photo_id=job.photo_id,
                        worker_id=job.worker_id,
                        provider=_METRICS_PROVIDER,
                        model=model,
                        status="FAILED",
                        latency_ms=int((time.perf_counter() - infer_t0) * 1000),
                        error_code=type(e).__name__,
                    ),
                )
                return {
                    "status": "error",
                    "error": str(e),
                    "text": "",
                    "model": model,
                    "metadata": {"queue_wait_sec": wait, "degraded": degraded},
                }

        return {
            "status": "error",
            "error": "VLM inference exhausted retries",
            "text": "",
            "model": model,
            "metadata": {"queue_wait_sec": wait, "degraded": degraded},
        }
