"""P0 scheduler for Agent → model HTTP (not Stage3 VLM, not skills).

One process-local lane: bounded admission, min-heap priority, queue-wait timeout.
``ChatFn`` stays ``(messages) -> str``; LangGraph does not know about this module.

Kinds (smaller dequeues first): ``interactive`` < ``repair`` < ``batch``.
"""
from __future__ import annotations

import itertools
import logging
import os
import queue
import threading
import time
import uuid
from concurrent import futures as concurrent_futures
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from services.agent.conversation import ChatFn

logger = logging.getLogger(__name__)

KIND_INTERACTIVE = "interactive"
KIND_REPAIR = "repair"
KIND_BATCH = "batch"
_KIND_PRIORITY = {KIND_INTERACTIVE: 0, KIND_REPAIR: 1, KIND_BATCH: 2}

DEFAULT_MAX_QUEUE = 4
DEFAULT_WORKERS = 1
DEFAULT_WAIT_TIMEOUT = 12.0
SCHEDULER_ENV = "LIVEHOUSE_AGENT_INFER_SCHEDULER"


class AgentInferRejected(RuntimeError):
    """Admission full: queued + in-flight already at ``max_queue_size``."""

    def __init__(self) -> None:
        super().__init__("agent infer queue full")


class AgentInferExpired(RuntimeError):
    """Sat in the queue longer than ``queue_wait_timeout`` without a worker."""

    def __init__(self) -> None:
        super().__init__("agent infer queue wait expired")


def _kind_priority(kind: str) -> int:
    return _KIND_PRIORITY.get(str(kind or "").strip().lower(), _KIND_PRIORITY[KIND_INTERACTIVE])


def scheduler_enabled() -> bool:
    raw = (os.environ.get(SCHEDULER_ENV) or "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


@dataclass
class _Job:
    fn: ChatFn
    messages: list[dict[str, Any]]
    kind: str
    request_id: str
    enqueued_mono: float
    cancelled: bool = False
    started: bool = False
    released: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)
    admitted: threading.Event = field(default_factory=threading.Event)
    future: concurrent_futures.Future[str] = field(default_factory=concurrent_futures.Future)


class AgentInferScheduler:
    """In-process priority queue in front of a ChatFn."""

    def __init__(
        self,
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE,
        num_workers: int = DEFAULT_WORKERS,
        queue_wait_timeout: float = DEFAULT_WAIT_TIMEOUT,
    ) -> None:
        self.max_queue_size = max(1, int(max_queue_size))
        self.num_workers = max(1, int(num_workers))
        self.queue_wait_timeout = max(0.05, float(queue_wait_timeout))
        self._pq: queue.PriorityQueue[tuple[int, int, _Job]] = queue.PriorityQueue()
        self._admission = threading.BoundedSemaphore(self.max_queue_size)
        self._seq = itertools.count()
        self._stop = threading.Event()
        self._metrics_lock = threading.Lock()
        self._depth = 0
        self._active = 0
        self._rejected = 0
        self._expired = 0
        self._completed = 0
        self._last_wait_ms = 0
        self._workers = [
            threading.Thread(target=self._worker_loop, name=f"agent-infer-{i}", daemon=True)
            for i in range(self.num_workers)
        ]
        for t in self._workers:
            t.start()

    @classmethod
    def from_env(cls) -> AgentInferScheduler:
        return cls(
            max_queue_size=int(os.environ.get("LIVEHOUSE_AGENT_INFER_MAX_QUEUE") or DEFAULT_MAX_QUEUE),
            num_workers=int(os.environ.get("LIVEHOUSE_AGENT_INFER_WORKERS") or DEFAULT_WORKERS),
            queue_wait_timeout=float(
                os.environ.get("LIVEHOUSE_AGENT_INFER_WAIT_TIMEOUT") or DEFAULT_WAIT_TIMEOUT
            ),
        )

    def snapshot(self) -> dict[str, Any]:
        with self._metrics_lock:
            return {
                "depth": self._depth,
                "active": self._active,
                "max_queue_size": self.max_queue_size,
                "num_workers": self.num_workers,
                "rejected": self._rejected,
                "expired": self._expired,
                "completed": self._completed,
                "last_wait_ms": self._last_wait_ms,
            }

    def wrap_chat_fn(self, inner: ChatFn, *, kind: str = KIND_INTERACTIVE) -> ChatFn:
        def _chat(messages: list[dict[str, Any]]) -> str:
            return self.submit(inner, messages, kind=kind)

        return _chat

    def submit(
        self,
        fn: ChatFn,
        messages: list[dict[str, Any]],
        *,
        kind: str = KIND_INTERACTIVE,
        request_id: str | None = None,
    ) -> str:
        if self._stop.is_set():
            raise AgentInferRejected()
        if not self._admission.acquire(blocking=False):
            with self._metrics_lock:
                self._rejected += 1
            raise AgentInferRejected()
        job = _Job(
            fn=fn,
            messages=list(messages or []),
            kind=str(kind or KIND_INTERACTIVE),
            request_id=(request_id or uuid.uuid4().hex[:12]),
            enqueued_mono=time.monotonic(),
        )
        with self._metrics_lock:
            self._depth += 1
        self._pq.put((_kind_priority(job.kind), next(self._seq), job))
        if not job.admitted.wait(timeout=self.queue_wait_timeout):
            if job.admitted.is_set():
                return job.future.result()
            self._expire(job)
            raise AgentInferExpired()
        return job.future.result()

    def close(self) -> None:
        self._stop.set()
        for t in self._workers:
            t.join(timeout=1.0)

    def _expire(self, job: _Job) -> None:
        with job.lock:
            job.cancelled = True
            started = job.started
        if not started:
            self._release(job)
        with self._metrics_lock:
            self._expired += 1

    def _release(self, job: _Job) -> None:
        with job.lock:
            if job.released:
                return
            job.released = True
        try:
            self._admission.release()
        except ValueError:
            pass
        with self._metrics_lock:
            self._depth = max(0, self._depth - 1)

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                _prio, _seq, job = self._pq.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with job.lock:
                    if job.cancelled:
                        skip = True
                    else:
                        job.started = True
                        skip = False
                if skip:
                    continue
                job.admitted.set()
                wait_ms = int(max(0.0, time.monotonic() - job.enqueued_mono) * 1000)
                with self._metrics_lock:
                    self._active += 1
                    self._last_wait_ms = wait_ms
                try:
                    job.future.set_result(job.fn(job.messages))
                    with self._metrics_lock:
                        self._completed += 1
                except Exception as exc:
                    job.future.set_exception(exc)
                finally:
                    with self._metrics_lock:
                        self._active = max(0, self._active - 1)
            finally:
                self._release(job)
                self._pq.task_done()


_DEFAULT: AgentInferScheduler | None = None
_DEFAULT_LOCK = threading.Lock()


def default_scheduler() -> AgentInferScheduler:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = AgentInferScheduler.from_env()
        return _DEFAULT


def reset_default_scheduler() -> None:
    """Test helper: drop the process singleton."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is not None:
            _DEFAULT.close()
        _DEFAULT = None


def wrap_chat_fn(inner: ChatFn, *, kind: str = KIND_INTERACTIVE) -> ChatFn:
    if not scheduler_enabled():
        return inner
    return default_scheduler().wrap_chat_fn(inner, kind=kind)


def snapshot() -> Mapping[str, Any]:
    if _DEFAULT is None:
        return {
            "depth": 0,
            "active": 0,
            "rejected": 0,
            "expired": 0,
            "completed": 0,
            "last_wait_ms": 0,
        }
    return default_scheduler().snapshot()
