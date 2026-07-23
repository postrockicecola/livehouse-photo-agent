"""Priority inference queue with optional dynamic batching and per-model lanes.

Workers drain per-lane priority queues (``inference_lanes`` in config), optionally coalescing
compatible jobs for up to ``batch_aggregate_window_ms`` (when the router's primary provider
implements :meth:`~inference.providers.base.InferenceProvider.generate_batch`). When
``inference_lanes`` is omitted, a single lane is used for the router's primary model (legacy
behavior: one shared queue + ``max_concurrent_requests`` / ``max_inference_queue_size``).

Workers run synchronous HTTP ``provider.generate`` / ``generate_batch`` on daemon threads; this keeps the
pipeline ThreadPoolExecutor responsive while bounding parallel Ollama calls. Callers in async
code can use ``InferenceClient.predict_async`` (asyncio.to_thread) instead of rewriting
providers with aiohttp.
"""
from __future__ import annotations

import itertools
import logging
import os
import queue
import socket
import sqlite3
import threading
import time
from concurrent import futures as concurrent_futures
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, cast

from infra.metrics import (
    inference_queue_periodic_window_snapshot,
    inference_queue_runtime_snapshot,
    percentile_nearest_rank,
    snapshot_inference_queue_metrics,
)

from inference.router import InferenceRouter, RoundRobinInferenceRouter
from inference.types import InferenceRequest, InferenceResponse, inference_status_ok

RouterLike = InferenceRouter | RoundRobinInferenceRouter
from utils.logging_context import make_log_extra
from utils.luma_brain import (
    brain_connect,
    coerce_positive_job_id,
    create_model_run_and_mark_started,
    inference_request_payload_hash,
    mark_model_run_failed,
    mark_model_run_succeeded,
    replace_model_run_attempts,
    upsert_infra_runtime_snapshot,
)
from inference.ledger import (
    ERROR_TIMEOUT,
    classify_inference_error,
    compute_outcome_attribution,
)

logger = logging.getLogger(__name__)

_SQLITE_BUSY_ATTEMPTS = 6


def _metadata_cache_hit(meta: dict[str, Any]) -> bool:
    if meta.get("outcome") == "cache_hit":
        return True
    ch = meta.get("cache_hit")
    return isinstance(ch, dict) and len(ch) > 0


def _coerce_token_count(value: Any) -> int | None:
    """Provider usage counts (e.g. Ollama ``eval_count``) → non-negative int, or ``None`` when absent."""
    if value is None:
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _sqlite_busy_retry(
    op: str,
    fn: Callable[[], Any],
    *,
    job_id: int | None,
) -> Any:
    """Retry on SQLite lock/busy (multi-writer contention); re-raise other errors."""
    for attempt in range(_SQLITE_BUSY_ATTEMPTS):
        if attempt:
            time.sleep(0.02 * (2 ** (attempt - 1)))
        try:
            return fn()
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("database is locked" in msg or "busy" in msg) and attempt < _SQLITE_BUSY_ATTEMPTS - 1:
                logger.warning(
                    "sqlite busy retry op=%s attempt=%s/%s job_id=%s: %s",
                    op,
                    attempt + 1,
                    _SQLITE_BUSY_ATTEMPTS,
                    job_id,
                    e,
                )
                continue
            raise


@dataclass
class _InferenceJob:
    request: InferenceRequest
    enqueued_mono: float = field(default_factory=time.monotonic)
    admitted_mono: float = field(default_factory=time.monotonic)
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    client_future: concurrent_futures.Future[dict[str, Any]] | None = None
    infer_latency_ms: int | None = None
    queue_wait_ms_for_log: int | None = None


@dataclass(frozen=True)
class InferenceModelLane:
    """Per-model (lane) worker count and admission cap within one process."""

    num_workers: int = 1
    max_queue_size: int = 16


@dataclass
class _LaneRuntime:
    lane_id: str
    pq: queue.PriorityQueue[tuple[int, int, _InferenceJob]]
    seq: itertools.count
    admission: threading.BoundedSemaphore
    num_workers: int
    max_queue_size: int
    workers: list[threading.Thread] = field(default_factory=list)
    cumulative_worker_idle_sec: float = 0.0
    active: int = 0


def _router_primary_model_name(router: RouterLike) -> str:
    return str(router.primary_model_name)


def _coerce_inference_lane_spec(
    value: InferenceModelLane | Mapping[str, Any] | Any,
    *,
    default_workers: int,
    default_max_queue: int,
) -> InferenceModelLane:
    if isinstance(value, InferenceModelLane):
        return InferenceModelLane(
            num_workers=max(1, int(value.num_workers)),
            max_queue_size=max(1, int(value.max_queue_size)),
        )
    if isinstance(value, Mapping):
        m = cast(Mapping[str, Any], value)
        nw = int(m.get("num_workers", default_workers) or default_workers)
        mq_raw = m.get("max_queue_size", m.get("max_inference_queue_size", default_max_queue))
        mq = int(mq_raw if mq_raw is not None else default_max_queue)
        return InferenceModelLane(num_workers=max(1, nw), max_queue_size=max(1, mq))
    return InferenceModelLane(
        num_workers=max(1, int(default_workers)),
        max_queue_size=max(1, int(default_max_queue)),
    )


def _build_lane_specs(
    router: RouterLike,
    *,
    num_workers: int,
    max_queue_size: int,
    inference_lanes: Mapping[str, InferenceModelLane | Mapping[str, Any]] | None,
) -> dict[str, InferenceModelLane]:
    """Lane id → sizing. Always includes the router primary model as a routable lane."""
    primary = _router_primary_model_name(router)
    nw = max(1, int(num_workers))
    mq = max(1, int(max_queue_size))
    if not inference_lanes:
        return {primary: InferenceModelLane(num_workers=nw, max_queue_size=mq)}
    specs: dict[str, InferenceModelLane] = {}
    for raw_key, raw_val in inference_lanes.items():
        lid = str(raw_key).strip()
        if not lid:
            continue
        specs[lid] = _coerce_inference_lane_spec(raw_val, default_workers=nw, default_max_queue=mq)
    if primary not in specs:
        specs[primary] = InferenceModelLane(num_workers=nw, max_queue_size=mq)
        logger.info(
            "inference_lanes missing primary model %r; added lane num_workers=%s max_queue_size=%s",
            primary,
            nw,
            mq,
        )
    return specs


class PrioritizedInferenceQueue:
    """Queue runner for provider-backed inference requests (per-model lanes optional)."""

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
        backpressure_log_seconds: float = 0.5,
        max_batch_size: int = 1,
        batch_aggregate_window_ms: float = 0.0,
        inference_lanes: Mapping[str, InferenceModelLane | Mapping[str, Any]] | None = None,
    ) -> None:
        self.router = router
        self.queue_wait_timeout_seconds = float(queue_wait_timeout_seconds)
        self.fallback_num_predict = fallback_num_predict
        self.max_retries = max_retries
        self.timeout = timeout
        self.max_batch_size = max(1, int(max_batch_size))
        self.batch_aggregate_window_ms = max(0.0, float(batch_aggregate_window_ms))
        self._metrics_lock = threading.Lock()
        self._inference_read_timeout = (
            int(inference_hard_timeout_seconds)
            if inference_hard_timeout_seconds is not None
            else None
        )
        self._backpressure_log_seconds = float(backpressure_log_seconds)
        self._last_batch_infer_wall_sec = 0.0

        self._stop = threading.Event()
        nw = max(1, int(num_workers))
        mq = max(1, int(max_queue_size))
        lane_specs = _build_lane_specs(
            router,
            num_workers=nw,
            max_queue_size=mq,
            inference_lanes=inference_lanes,
        )
        primary = _router_primary_model_name(router)
        self._primary_lane_id = primary
        self._multi_lane = len(lane_specs) > 1
        lane_ids_sorted = sorted(lane_specs.keys(), key=lambda x: (0 if x == primary else 1, x))
        self._lanes: dict[str, _LaneRuntime] = {}
        self._lanes_ordered: list[_LaneRuntime] = []
        sum_mq = 0
        sum_nw = 0
        for lid in lane_ids_sorted:
            spec = lane_specs[lid]
            lmq = spec.max_queue_size
            lnw = spec.num_workers
            sum_mq += lmq
            sum_nw += lnw
            if lmq < lnw:
                logger.warning(
                    "inference lane %r: max_queue_size=%s < num_workers=%s",
                    lid,
                    lmq,
                    lnw,
                )
            lane = _LaneRuntime(
                lane_id=lid,
                pq=queue.PriorityQueue(),
                seq=itertools.count(),
                admission=threading.BoundedSemaphore(lmq),
                num_workers=lnw,
                max_queue_size=lmq,
            )
            self._lanes[lid] = lane
            self._lanes_ordered.append(lane)
        self.max_queue_size = sum_mq
        self._num_workers = sum_nw
        self._batch_seq = itertools.count(1)
        self._periodic_stop = threading.Event()
        self._runtime_snap_min_interval = 2.0
        self._last_runtime_snap_mono = 0.0
        for lane in self._lanes_ordered:
            for i in range(lane.num_workers):
                t = threading.Thread(
                    target=self._worker_loop,
                    kwargs={"lane": lane, "worker_index": i},
                    name=f"inference-queue-{lane.lane_id}-{i}",
                    daemon=True,
                )
                t.start()
                lane.workers.append(t)
        self._periodic_thread = threading.Thread(
            target=self._periodic_metrics_loop,
            name="inference-queue-periodic",
            daemon=True,
        )
        self._periodic_thread.start()
        logger.info(
            "Inference queue started | lanes=%s total_workers=%s total_max_inflight=%s "
            "hard_timeout=%s batch=%s window_ms=%s",
            list(lane_specs.keys()),
            sum_nw,
            sum_mq,
            self._inference_read_timeout,
            self.max_batch_size,
            self.batch_aggregate_window_ms,
        )
        snapshot_inference_queue_metrics(
            depth=0, active=0, max_inflight=sum_mq, num_workers=sum_nw
        )

    def observability_snapshot(self) -> dict[str, Any]:
        """Point-in-time stats for logging / Stage3 saturation checks (not Prometheus)."""
        with self._metrics_lock:
            depth = 0
            active = 0
            idle_sum = 0.0
            lanes: dict[str, Any] = {}
            for lane in self._lanes_ordered:
                depth += lane.pq.qsize()
                active += lane.active
                idle_sum += lane.cumulative_worker_idle_sec
                lanes[lane.lane_id] = {
                    "queue_size": int(lane.pq.qsize()),
                    "active_workers": int(lane.active),
                    "idle_time": round(float(lane.cumulative_worker_idle_sec), 4),
                    "max_inflight": int(lane.max_queue_size),
                    "num_workers": int(lane.num_workers),
                }
            snap = {
                "queue_size": int(depth),
                "active_workers": int(active),
                "idle_time": round(float(idle_sum), 4),
                "max_inflight": int(self.max_queue_size),
                "max_batch_size": int(self.max_batch_size),
                "batch_window_ms": float(self.batch_aggregate_window_ms),
                "router_batch_capable": bool(self._router_supports_batch()),
                "primary_lane": self._primary_lane_id,
                "multi_lane": self._multi_lane,
                "lanes": lanes,
            }
        snap.update(inference_queue_runtime_snapshot())
        return snap

    def shutdown(self, *, cancel_queued: bool = True) -> None:
        """Stop admitting work; optionally fail futures still waiting in lane queues.

        In-flight inference (already claimed by a worker thread) is not aborted — HTTP calls
        run to completion or until the provider client timeout. Callers with a cancelled SSOT
        job should treat late results as best-effort.
        """
        self._stop.set()
        self._periodic_stop.set()
        if cancel_queued:
            self._reject_queued_jobs(reason="inference queue shutdown")

    def _reject_queued_jobs(self, *, reason: str) -> None:
        err: dict[str, Any] = {
            "status": "error",
            "error": reason,
            "text": "",
            "model": "",
        }
        for lane in self._lanes_ordered:
            while True:
                try:
                    _prio, _seq, job = lane.pq.get_nowait()
                except queue.Empty:
                    break
                job.result = err
                cfut = job.client_future
                if cfut is not None and not cfut.done():
                    cfut.set_result(err)
                job.done.set()
                try:
                    lane.admission.release()
                except Exception:
                    pass
        self._publish_metrics()

    def _periodic_metrics_loop(self) -> None:
        while not self._periodic_stop.is_set():
            if self._periodic_stop.wait(5.0):
                break
            try:
                snap = inference_queue_periodic_window_snapshot(window_sec=5.0)
            except Exception:
                logger.exception("inference queue periodic snapshot failed")
                continue
            logger.info(
                "inference queue periodic | pool_workers=%s max_inflight=%s window_sec=%s",
                snap["num_workers"],
                snap["max_inflight"],
                snap["window_sec"],
                extra=make_log_extra(
                    status="PERIODIC",
                    queue_size=snap["queue_size"],
                    inflight=snap["inflight"],
                    pending=snap["pending"],
                    p95_queue_wait_ms=snap["p95_queue_wait_ms"],
                    inference_per_sec=snap["inference_per_sec"],
                    gpu_util=snap["gpu_util"],
                ),
            )

    def _publish_metrics(self) -> None:
        with self._metrics_lock:
            depth = 0
            active = 0
            for lane in self._lanes_ordered:
                depth += lane.pq.qsize()
                active += lane.active
        snapshot_inference_queue_metrics(
            depth=depth,
            active=active,
            max_inflight=self.max_queue_size,
            num_workers=self._num_workers,
        )
        self._maybe_persist_inference_queue_snapshot(depth=depth, active=active)

    def _maybe_persist_inference_queue_snapshot(self, *, depth: int, active: int) -> None:
        now_m = time.monotonic()
        if now_m - self._last_runtime_snap_mono < self._runtime_snap_min_interval:
            return
        self._last_runtime_snap_mono = now_m
        src = (os.environ.get("LIVEHOUSE_RUNTIME_METRICS_SOURCE") or "").strip() or (
            f"{socket.gethostname()}:{os.getpid()}"
        )
        payload = {
            "depth": int(depth),
            "active_workers": int(active),
            "max_inflight": int(self.max_queue_size),
            "lanes": {
                lane.lane_id: {
                    "depth": int(lane.pq.qsize()),
                    "active_workers": int(lane.active),
                    "max_inflight": int(lane.max_queue_size),
                }
                for lane in self._lanes_ordered
            },
        }
        try:
            conn = brain_connect()
            try:
                upsert_infra_runtime_snapshot(conn, source=src, component="inference_queue", payload=payload)
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover - avoid stalling hot path
            logger.debug("infra runtime snapshot skipped: %s", exc)

    def _client_wait_cap(self) -> float:
        attempts = self.max_retries + 1
        return max(900.0, self.queue_wait_timeout_seconds + 120.0 + self.timeout * attempts * 2)

    def _lane_for_request(self, request: InferenceRequest) -> _LaneRuntime:
        wanted = (request.model_name or "").strip()
        effective = wanted or self._primary_lane_id
        lane = self._lanes.get(effective)
        if lane is not None:
            return lane
        if self._multi_lane and wanted and wanted != self._primary_lane_id:
            logger.debug(
                "inference lane miss model=%r → primary lane %r",
                wanted,
                self._primary_lane_id,
            )
        return self._lanes[self._primary_lane_id]

    def submit_future(
        self,
        image_path: str,
        prompt: str,
        priority: int = 0,
        trace_id: str | None = None,
        job_id: int | None = None,
        session_id: int | None = None,
        photo_id: int | None = None,
        worker_id: int | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        metadata_extra: dict[str, Any] | None = None,
    ) -> concurrent_futures.Future[dict[str, Any]]:
        """Enqueue one job and return immediately; await ``.result()`` for the payload dict."""
        if self._stop.is_set():
            fut: concurrent_futures.Future[dict[str, Any]] = concurrent_futures.Future()
            fut.set_result(
                {
                    "status": "error",
                    "error": "inference queue shutting down",
                    "text": "",
                    "model": "",
                }
            )
            return fut
        md = {
            "trace_id": trace_id,
            "job_id": job_id,
            "session_id": session_id,
            "photo_id": photo_id,
            "worker_id": worker_id,
            "provider": provider,
        }
        if metadata_extra:
            md.update(metadata_extra)
        try:
            from infra.scope_quota import admit_vlm_for_scope
            from utils.luma_brain import dispatch_scope_from_env, get_job

            ns = md.get("namespace")
            pk = md.get("project_key")
            if (ns is None or pk is None) and job_id is not None:
                try:
                    jid = int(job_id)
                except (TypeError, ValueError):
                    jid = None
                if jid is not None:
                    conn = brain_connect()
                    try:
                        row = get_job(conn, job_id=jid)
                    finally:
                        conn.close()
                    if row:
                        ns = ns if ns is not None else row.get("namespace")
                        pk = pk if pk is not None else row.get("project_key")
            if ns is None and pk is None:
                ns, pk = dispatch_scope_from_env()
            gate = admit_vlm_for_scope(
                namespace=str(ns) if ns is not None else None,
                project_key=str(pk) if pk is not None else None,
            )
            if not gate.get("ok"):
                fut = concurrent_futures.Future()
                fut.set_result(
                    {
                        "status": "error",
                        "error": str(gate.get("error") or "scope_vlm_quota_exceeded"),
                        "text": "",
                        "model": "",
                        "scope_quota": gate,
                    }
                )
                return fut
        except Exception as quota_exc:
            # Default fail-closed: a broken quota path must not become unbounded VLM.
            # Escape hatch for emergency demos: LIVEHOUSE_SCOPE_QUOTA_FAIL_OPEN=1.
            fail_open = (os.environ.get("LIVEHOUSE_SCOPE_QUOTA_FAIL_OPEN") or "").strip() in (
                "1",
                "true",
                "TRUE",
                "yes",
                "YES",
            )
            if fail_open:
                logger.exception(
                    "scope quota check failed; allowing admit (fail-open escape hatch)"
                )
            else:
                logger.exception("scope quota check failed; denying admit (fail-closed)")
                fut = concurrent_futures.Future()
                fut.set_result(
                    {
                        "status": "error",
                        "error": "scope_quota_check_failed",
                        "text": "",
                        "model": "",
                        "scope_quota": {
                            "ok": False,
                            "enforced": True,
                            "error": "scope_quota_check_failed",
                            "detail": str(quota_exc)[:200],
                        },
                    }
                )
                return fut
        req = InferenceRequest(
            image_path=image_path,
            prompt=prompt,
            priority=priority,
            model_name=model_name,
            metadata=md,
        )
        lane = self._lane_for_request(req)
        t_adm0 = time.monotonic()
        lane.admission.acquire()
        if self._stop.is_set():
            lane.admission.release()
            fut = concurrent_futures.Future()
            fut.set_result(
                {
                    "status": "error",
                    "error": "inference queue shutting down",
                    "text": "",
                    "model": "",
                }
            )
            return fut
        bp_wait = time.monotonic() - t_adm0
        if bp_wait >= self._backpressure_log_seconds:
            logger.info(
                "inference queue backpressure: waited %.2fs for inflight slot "
                "(lane=%s max_inflight=%s)",
                bp_wait,
                lane.lane_id,
                lane.max_queue_size,
                extra=make_log_extra(
                    trace_id=req.metadata.get("trace_id"),
                    job_id=req.metadata.get("job_id"),
                    session_id=req.metadata.get("session_id"),
                    photo_id=req.metadata.get("photo_id"),
                    worker_id=req.metadata.get("worker_id"),
                    provider=req.metadata.get("provider"),
                    model=req.model_name,
                    inference_lane=lane.lane_id,
                    status="BACKPRESSURE",
                ),
            )
        admit_mono = time.monotonic()
        fut: concurrent_futures.Future[dict[str, Any]] = concurrent_futures.Future()
        job = _InferenceJob(
            request=req,
            enqueued_mono=admit_mono,
            admitted_mono=admit_mono,
            client_future=fut,
        )
        lane.pq.put((priority, next(lane.seq), job))
        self._publish_metrics()
        logger.debug(
            "inference queued",
            extra=make_log_extra(
                trace_id=req.metadata.get("trace_id"),
                job_id=req.metadata.get("job_id"),
                session_id=req.metadata.get("session_id"),
                photo_id=req.metadata.get("photo_id"),
                worker_id=req.metadata.get("worker_id"),
                provider=req.metadata.get("provider"),
                model=req.model_name,
                inference_lane=lane.lane_id,
                status="QUEUED",
            ),
        )
        return fut

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
        provider: str | None = None,
        model_name: str | None = None,
        metadata_extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        fut = self.submit_future(
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
            metadata_extra=metadata_extra,
        )
        cap = self._client_wait_cap()
        try:
            return fut.result(timeout=cap)
        except concurrent_futures.TimeoutError:
            return {
                "status": "error",
                "error": f"Inference queue client wait exceeded {cap:.0f}s",
                "text": "",
                "model": "",
            }

    def _router_supports_batch(self) -> bool:
        fn = getattr(self.router, "supports_batch_inference", None)
        return bool(fn()) if callable(fn) else False

    def _batch_coalescing_enabled(self) -> bool:
        return (
            self.max_batch_size > 1
            and self.batch_aggregate_window_ms > 0.0
            and self._router_supports_batch()
        )

    @staticmethod
    def _batch_key_for(job: _InferenceJob) -> tuple[Any, ...]:
        r = job.request
        md = r.metadata
        npv = md.get("num_predict", "__default__")
        prov = str(md.get("provider") or "")
        m = r.model_name or ""
        return (r.prompt, m, npv, prov)

    def _batch_eligible(self, job: _InferenceJob) -> bool:
        return (time.monotonic() - job.enqueued_mono) <= self.queue_wait_timeout_seconds

    def _collect_jobs_for_lane(self, lane: _LaneRuntime) -> list[_InferenceJob]:
        t_wait = time.monotonic()
        try:
            _pri, _seq, job = lane.pq.get(timeout=0.5)
        except queue.Empty:
            dt = time.monotonic() - t_wait
            with self._metrics_lock:
                lane.cumulative_worker_idle_sec += dt
            return []
        if not self._batch_coalescing_enabled() or not self._batch_eligible(job):
            return [job]
        key = self._batch_key_for(job)
        batch: list[_InferenceJob] = [job]
        deadline = time.monotonic() + self.batch_aggregate_window_ms / 1000.0
        deferred: list[tuple[int, int, _InferenceJob]] = []
        while len(batch) < self.max_batch_size:
            if time.monotonic() >= deadline:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                it = lane.pq.get(block=True, timeout=min(0.05, max(remaining, 0.001)))
            except queue.Empty:
                continue
            pri, seq, j2 = it
            if self._batch_key_for(j2) != key or not self._batch_eligible(j2):
                deferred.append(it)
                continue
            batch.append(j2)
        for it in deferred:
            lane.pq.put(it)
        return batch

    def _prepare_infer_request(self, job: _InferenceJob) -> InferenceRequest:
        md_base = dict(job.request.metadata)
        if self._inference_read_timeout is not None:
            md_base["inference_read_timeout_seconds"] = self._inference_read_timeout
        return replace(job.request, metadata=md_base)

    def _ledger_create_run_start(
        self,
        job: _InferenceJob,
        infer_req: InferenceRequest,
        *,
        wait_sec: float,
    ) -> int | None:
        md = infer_req.metadata
        raw_job_id = md.get("job_id")
        ledger_job_id = coerce_positive_job_id(raw_job_id)
        queue_wait_ms = max(0, int(wait_sec * 1000))
        provider = str(md.get("provider") or "unknown")
        run_id: int | None = None
        r = self.router
        pprov = r.primary_provider.provider_id
        fprov = r.fallback_provider.provider_id
        pmodel = job.request.model_name or r.primary_model_name
        prompt_len = len(job.request.prompt or "")
        if ledger_job_id is not None:
            phash = inference_request_payload_hash(
                prompt=job.request.prompt,
                image_path=job.request.image_path,
                model_name=job.request.model_name,
            )

            def _open_ledger() -> int:
                conn = brain_connect()
                try:
                    return create_model_run_and_mark_started(
                        conn,
                        job_id=ledger_job_id,
                        provider=provider,
                        model_name=job.request.model_name,
                        request_payload_hash=phash,
                        primary_provider=pprov,
                        fallback_provider=fprov,
                        primary_model=pmodel,
                        prompt_length=prompt_len,
                        queue_wait_ms=queue_wait_ms,
                    )
                finally:
                    conn.close()

            try:
                run_id = _sqlite_busy_retry(
                    "inference_ledger_create_start",
                    _open_ledger,
                    job_id=ledger_job_id,
                )
            except Exception:
                logger.exception(
                    "model_runs ledger create/start failed (job_id=%s raw_job_id=%r thread=%s)",
                    ledger_job_id,
                    raw_job_id,
                    threading.current_thread().name,
                )
                raise
        elif raw_job_id is not None:
            logger.debug(
                "skipping model_runs ledger: invalid job_id placeholder raw_job_id=%r thread=%s",
                raw_job_id,
                threading.current_thread().name,
            )
        return run_id

    def _infer_fail_ledger(
        self,
        job: _InferenceJob,
        infer_req: InferenceRequest,
        run_id: int,
        infer_t0: float,
        force_fallback: bool,
        exc: BaseException,
    ) -> None:
        md = infer_req.metadata
        raw_job_id = md.get("job_id")
        ledger_job_id = coerce_positive_job_id(raw_job_id)
        r = self.router
        pprov = r.primary_provider.provider_id
        fprov = r.fallback_provider.provider_id
        pmodel = job.request.model_name or r.primary_model_name

        def _fail_ledger() -> None:
            conn = brain_connect()
            try:
                et = classify_inference_error(exc)
                ms = "TIMEOUT" if et == ERROR_TIMEOUT else "FAILED"
                replace_model_run_attempts(conn, model_run_id=run_id, attempts=[])
                mark_model_run_failed(
                    conn,
                    run_id=run_id,
                    latency_ms=max(0, int((time.perf_counter() - infer_t0) * 1000)),
                    end_to_end_latency_ms=max(0, int((time.perf_counter() - infer_t0) * 1000)),
                    provider_latency_ms=max(0, int((time.perf_counter() - infer_t0) * 1000)),
                    error_message=str(exc),
                    error_type=et,
                    degraded=1 if force_fallback else 0,
                    model_name=job.request.model_name,
                    primary_provider=pprov,
                    fallback_provider=fprov,
                    primary_model=pmodel,
                    fallback_used=1 if force_fallback else 0,
                    status=ms,
                    outcome_attribution="exception",
                )
            finally:
                conn.close()

        try:
            _sqlite_busy_retry(
                "inference_ledger_fail_infer_exception",
                _fail_ledger,
                job_id=ledger_job_id,
            )
        except Exception:
            logger.exception(
                "model_runs mark failed after infer exception (run_id=%s job_id=%s thread=%s)",
                run_id,
                ledger_job_id,
                threading.current_thread().name,
            )

    def _finalize_infer_payload(
        self,
        job: _InferenceJob,
        infer_req: InferenceRequest,
        response: InferenceResponse,
        *,
        run_id: int | None,
        infer_t0: float,
        wait_sec: float,
        force_fallback: bool,
    ) -> dict[str, Any]:
        md = infer_req.metadata
        raw_job_id = md.get("job_id")
        ledger_job_id = coerce_positive_job_id(raw_job_id)
        r = self.router
        pprov = r.primary_provider.provider_id
        fprov = r.fallback_provider.provider_id
        pmodel = job.request.model_name or r.primary_model_name

        payload = response.to_dict()
        meta = dict(payload.get("metadata") or {})
        ledger = dict(meta.get("inference_ledger") or {})
        meta["queue_wait_sec"] = wait_sec
        meta["degraded"] = bool(force_fallback or ledger.get("router_fallback_used"))
        payload["metadata"] = meta
        ok_inference = inference_status_ok(str(payload.get("status") or ""))
        latency_ms = int((time.perf_counter() - infer_t0) * 1000)
        status_upper = str(payload.get("status") or "error").strip().upper()
        job.infer_latency_ms = latency_ms
        job.queue_wait_ms_for_log = max(0, int(wait_sec * 1000))
        attempts = list(ledger.get("attempts") or [])
        http_retries = max(0, len(attempts) - 1) if attempts else 0
        meta["pipeline_inference"] = {
            "queue_wait_ms": job.queue_wait_ms_for_log,
            "infer_latency_ms": latency_ms,
            "router_fallback_used": bool(ledger.get("router_fallback_used") or force_fallback),
            "http_retry_count": http_retries,
            "cache_hit": _metadata_cache_hit(meta),
            "image_trace_id": md.get("image_trace_id"),
            "job_trace_id": md.get("job_trace_id"),
            "pipeline_image": md.get("pipeline_image"),
        }
        if not ok_inference:
            log_status = "FAILED"
            logger.warning(
                "inference request failed",
                extra=make_log_extra(
                    trace_id=md.get("trace_id"),
                    job_id=md.get("job_id"),
                    session_id=md.get("session_id"),
                    photo_id=md.get("photo_id"),
                    worker_id=md.get("worker_id"),
                    provider=md.get("provider"),
                    model=payload.get("model") or job.request.model_name,
                    status=log_status,
                    latency_ms=latency_ms,
                    queue_wait_ms=job.queue_wait_ms_for_log,
                    error_code=(payload.get("error") or "")[:64],
                ),
            )
        elif status_upper == "DEGRADED":
            logger.debug(
                "inference request degraded",
                extra=make_log_extra(
                    trace_id=md.get("trace_id"),
                    job_id=md.get("job_id"),
                    session_id=md.get("session_id"),
                    photo_id=md.get("photo_id"),
                    worker_id=md.get("worker_id"),
                    provider=md.get("provider"),
                    model=payload.get("model") or job.request.model_name,
                    status="DEGRADED",
                    latency_ms=latency_ms,
                    queue_wait_ms=job.queue_wait_ms_for_log,
                ),
            )

        if run_id is not None:

            def _terminal_ledger() -> None:
                conn = brain_connect()
                try:
                    replace_model_run_attempts(
                        conn,
                        model_run_id=run_id,
                        attempts=list(ledger.get("attempts") or []),
                    )
                    degraded_i = 1 if meta.get("degraded") else 0
                    model_used = payload.get("model") or job.request.model_name
                    e2e_ledger = ledger.get("end_to_end_latency_ms")
                    prov_ledger = ledger.get("provider_latency_ms")
                    e2e = int(e2e_ledger) if e2e_ledger is not None else latency_ms
                    prov = int(prov_ledger) if prov_ledger is not None else e2e
                    fb_u = bool(ledger.get("router_fallback_used") or force_fallback)
                    resp_len = len(str(payload.get("text") or ""))
                    prompt_tok = _coerce_token_count(meta.get("prompt_eval_count"))
                    completion_tok = _coerce_token_count(meta.get("eval_count"))
                    outcome = compute_outcome_attribution(
                        ledger=ledger, payload_status=status_upper if ok_inference else "FAILED"
                    )
                    if ok_inference:
                        mark_model_run_succeeded(
                            conn,
                            run_id=run_id,
                            latency_ms=e2e,
                            end_to_end_latency_ms=e2e,
                            provider_latency_ms=prov,
                            degraded=degraded_i,
                            fallback_used=1 if fb_u else 0,
                            model_name=model_used,
                            final_model=ledger.get("final_model") or model_used,
                            primary_provider=ledger.get("primary_provider") or pprov,
                            fallback_provider=ledger.get("fallback_provider") or fprov,
                            primary_model=ledger.get("primary_model") or pmodel,
                            response_length=resp_len,
                            prompt_tokens=prompt_tok,
                            completion_tokens=completion_tok,
                            outcome_attribution=outcome,
                        )
                    else:
                        et = (payload.get("error_type") or ledger.get("error_type")) or classify_inference_error(
                            error_message=str(payload.get("error") or "inference error")
                        )
                        st = "TIMEOUT" if et == ERROR_TIMEOUT else "FAILED"
                        mark_model_run_failed(
                            conn,
                            run_id=run_id,
                            latency_ms=e2e,
                            end_to_end_latency_ms=e2e,
                            provider_latency_ms=prov,
                            error_message=str(payload.get("error") or "inference error"),
                            error_type=et,
                            degraded=degraded_i,
                            model_name=model_used,
                            final_model=ledger.get("final_model") or model_used,
                            primary_provider=ledger.get("primary_provider") or pprov,
                            fallback_provider=ledger.get("fallback_provider") or fprov,
                            primary_model=ledger.get("primary_model") or pmodel,
                            fallback_used=1 if fb_u else 0,
                            response_length=resp_len,
                            status=st,
                            outcome_attribution=outcome,
                        )
                finally:
                    conn.close()

            try:
                _sqlite_busy_retry(
                    "inference_ledger_terminal",
                    _terminal_ledger,
                    job_id=ledger_job_id,
                )
            except Exception:
                logger.exception(
                    "model_runs terminal update failed (run_id=%s job_id=%s thread=%s); "
                    "inference response still returned",
                    run_id,
                    ledger_job_id,
                    threading.current_thread().name,
                )
                meta["inference_ledger_write_failed"] = True

        return payload

    def _run_job_once(self, job: _InferenceJob) -> dict[str, Any]:
        infer_req = self._prepare_infer_request(job)
        wait = time.monotonic() - job.enqueued_mono
        force_fallback = wait > self.queue_wait_timeout_seconds
        md = infer_req.metadata
        run_id = self._ledger_create_run_start(job, infer_req, wait_sec=wait)

        if force_fallback:
            logger.warning(
                "inference fallback",
                extra=make_log_extra(
                    trace_id=md.get("trace_id"),
                    job_id=md.get("job_id"),
                    session_id=md.get("session_id"),
                    photo_id=md.get("photo_id"),
                    worker_id=md.get("worker_id"),
                    provider=md.get("provider"),
                    model=job.request.model_name,
                    status="FALLBACK",
                ),
            )

        infer_t0 = time.perf_counter()
        try:
            response = self.router.infer(
                infer_req,
                force_fallback=force_fallback,
                fallback_num_predict=self.fallback_num_predict,
            )
        except Exception as exc:
            if run_id is not None:
                self._infer_fail_ledger(job, infer_req, run_id, infer_t0, force_fallback, exc)
            raise

        return self._finalize_infer_payload(
            job,
            infer_req,
            response,
            run_id=run_id,
            infer_t0=infer_t0,
            wait_sec=wait,
            force_fallback=force_fallback,
        )

    def _run_multi_jobs(self, jobs: list[_InferenceJob]) -> list[dict[str, Any]]:
        infer_reqs = [self._prepare_infer_request(j) for j in jobs]
        waits = [time.monotonic() - j.enqueued_mono for j in jobs]
        flags = [w > self.queue_wait_timeout_seconds for w in waits]
        run_ids: list[int | None] = []
        for job, ir, w in zip(jobs, infer_reqs, waits):
            run_ids.append(self._ledger_create_run_start(job, ir, wait_sec=w))
        n_fb = sum(1 for f in flags if f)
        if n_fb:
            md0 = infer_reqs[0].metadata
            logger.warning(
                "inference batch fallback (queue wait exceeded) count=%s",
                n_fb,
                extra=make_log_extra(
                    trace_id=md0.get("trace_id"),
                    job_id=md0.get("job_id"),
                    session_id=md0.get("session_id"),
                    photo_id=md0.get("photo_id"),
                    worker_id=md0.get("worker_id"),
                    provider=md0.get("provider"),
                    model=jobs[0].request.model_name,
                    status="FALLBACK",
                    batch_size=len(jobs),
                ),
            )
        infer_t0 = time.perf_counter()
        try:
            responses = self.router.infer_batch(
                infer_reqs,
                force_fallback_flags=flags,
                fallback_num_predict=self.fallback_num_predict,
            )
        except Exception as exc:
            for job, ir, rid, w, fb in zip(jobs, infer_reqs, run_ids, waits, flags):
                if rid is not None:
                    self._infer_fail_ledger(job, ir, rid, infer_t0, fb, exc)
            raise
        return [
            self._finalize_infer_payload(
                job,
                ir,
                resp,
                run_id=rid,
                infer_t0=infer_t0,
                wait_sec=w,
                force_fallback=fb,
            )
            for job, ir, resp, rid, w, fb in zip(jobs, infer_reqs, responses, run_ids, waits, flags)
        ]

    def _run_jobs(self, jobs: list[_InferenceJob]) -> list[dict[str, Any]]:
        infer_t0 = time.perf_counter()
        try:
            if len(jobs) == 1:
                payloads = [self._run_job_once(jobs[0])]
            else:
                payloads = self._run_multi_jobs(jobs)
        finally:
            self._last_batch_infer_wall_sec = time.perf_counter() - infer_t0
        return payloads

    def _worker_loop(self, *, lane: _LaneRuntime, worker_index: int) -> None:
        while not self._stop.is_set():
            jobs = self._collect_jobs_for_lane(lane)
            if not jobs:
                continue
            batch_id = f"ibatch_{next(self._batch_seq):010d}"
            for j in jobs:
                j.infer_latency_ms = None
                j.queue_wait_ms_for_log = None
            t_claim = time.monotonic()
            self._last_batch_infer_wall_sec = 0.0
            try:
                with self._metrics_lock:
                    lane.active += len(jobs)
                self._publish_metrics()
                payloads = self._run_jobs(jobs)
                for job, payload in zip(jobs, payloads):
                    job.result = payload
            except Exception as e:
                logger.exception("Inference queue worker error: %s", e)
                err: dict[str, Any] = {"status": "error", "error": str(e), "text": "", "model": ""}
                for job in jobs:
                    job.result = err
            finally:
                wall = float(self._last_batch_infer_wall_sec)
                with self._metrics_lock:
                    lane.active -= len(jobs)
                    qsz = sum(ln.pq.qsize() for ln in self._lanes_ordered)
                    infl = sum(ln.active for ln in self._lanes_ordered)
                pending = qsz + infl
                for job in jobs:
                    cfut = job.client_future
                    if cfut is not None and not cfut.done():
                        res = job.result
                        if res is not None:
                            cfut.set_result(res)
                        else:
                            cfut.set_result(
                                {
                                    "status": "error",
                                    "error": "Inference queue returned no result",
                                    "text": "",
                                    "model": "",
                                }
                            )
                    job.done.set()
                    lane.admission.release()
                    e2e_ms = int((time.monotonic() - job.admitted_mono) * 1000)
                    snapshot_inference_queue_metrics(job_e2e_ms=e2e_ms)
                qw_ms = int(
                    max(0.0, sum(t_claim - j.enqueued_mono for j in jobs) / max(1, len(jobs))) * 1000
                )
                logged_qw = [j.queue_wait_ms_for_log for j in jobs if j.queue_wait_ms_for_log is not None]
                batch_queue_wait_ms = int(sum(logged_qw) / len(logged_qw)) if logged_qw else qw_ms
                latencies = [j.infer_latency_ms for j in jobs if j.infer_latency_ms is not None]
                avg_lat = int(sum(latencies) / len(latencies)) if latencies else None
                p95_lat = percentile_nearest_rank(latencies, 0.95) if latencies else None
                thr = (len(jobs) / wall) if wall > 0.001 else None
                ok_payloads = [j.result for j in jobs if isinstance(j.result, dict)]
                hits = 0
                ok_infer = 0
                for pl in ok_payloads:
                    if inference_status_ok(str(pl.get("status") or "")):
                        ok_infer += 1
                        meta = pl.get("metadata") or {}
                        if isinstance(meta, dict) and _metadata_cache_hit(meta):
                            hits += 1
                cache_rate = (hits / ok_infer) if ok_infer else None
                failed = sum(
                    1
                    for j in jobs
                    if not isinstance(j.result, dict)
                    or not inference_status_ok(str((j.result or {}).get("status") or ""))
                )
                degraded_n = sum(
                    1
                    for j in jobs
                    if isinstance(j.result, dict)
                    and str(j.result.get("status") or "").strip().upper() == "DEGRADED"
                )
                logger.info(
                    "inference queue batch degraded=%s",
                    degraded_n,
                    extra=make_log_extra(
                        status="BATCH",
                        batch_id=batch_id,
                        worker_id=worker_index,
                        inference_lane=lane.lane_id,
                        batch_size=len(jobs),
                        total_processed=len(jobs),
                        queue_size=qsz,
                        inflight=infl,
                        pending=pending,
                        throughput_img_per_sec=round(thr, 4) if thr is not None else None,
                        avg_latency_ms=avg_lat,
                        p95_latency_ms=p95_lat,
                        gpu_busy_sec=round(wall, 4),
                        cache_hit_rate=round(cache_rate, 4) if cache_rate is not None else None,
                        queue_wait_ms=batch_queue_wait_ms,
                        p95_queue_wait_ms=percentile_nearest_rank(logged_qw, 0.95) if logged_qw else None,
                    ),
                )
                if failed:
                    logger.warning(
                        "inference queue batch failures=%s batch_id=%s",
                        failed,
                        batch_id,
                        extra=make_log_extra(
                            status="BATCH_PARTIAL_FAIL",
                            batch_id=batch_id,
                            worker_id=worker_index,
                            inference_lane=lane.lane_id,
                            batch_size=len(jobs),
                            total_processed=len(jobs),
                        ),
                    )
                snapshot_inference_queue_metrics(
                    batch_size=len(jobs),
                    queue_wait_ms=qw_ms,
                    infer_wall_sec=wall,
                    images_completed=len(jobs),
                )
                self._publish_metrics()
