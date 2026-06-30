"""
Process-local worker identity: registration (SSOT ``workers`` row) and heartbeat.

Celery workers reuse a cached ``worker_id`` per process to avoid hot-path writes;
``heartbeat`` still runs per task boundary for liveness. Long-running work (e.g. pipeline
``run()``) should wrap that section in ``long_task_heartbeat`` so ``requeue_stuck_jobs``
does not see a stale ``workers.last_heartbeat`` while the job is still making progress.
Operator controls (``PAUSED`` / ``DRAINING`` / ``ERROR``) are preserved: heartbeats with
``status=ONLINE`` do not clobber those rows (see ``utils.luma_brain.heartbeat_worker``).

**Executor class / pool:** ``WorkerManager`` stores the pool id in ``workers.worker_type``
(legacy name). Set ``LIVEHOUSE_EXECUTOR_CLASS`` (default ``general``) so a single Celery process
accepts every job kind; dedicated pools use values like ``inference`` or ``ingest`` — see
``services.worker_pools``.

**Admission capacity:** ``workers.capacity`` comes from :mod:`infra.worker_capacity` — binds Celery
concurrency (``CELERY_WORKER_CONCURRENCY`` / worker handle), optional ``LIVEHOUSE_WORKER_ADMISSION_CAP``,
``LIVEHOUSE_WORKER_POOL_CAPS``, and inference-side ceiling (``LIVEHOUSE_WORKER_INFERENCE_PROVIDER_SLOTS`` /
``LIVEHOUSE_MODEL_MAX_CONCURRENT_REQUESTS``). Live ``inflight`` for admission & dispatch headroom is derived
from ``jobs`` pipeline-active rows per worker — see ``utils.luma_brain.worker_runtime_admission``.
"""
from __future__ import annotations

import os
import socket
import threading
from contextlib import contextmanager
from typing import Any, Generator

from celery.signals import worker_process_init, worker_process_shutdown
from celery.utils.log import get_task_logger

from infra.worker_capacity import resolve_advertised_worker_capacity, resolve_celery_concurrency

logger = get_task_logger(__name__)

_WORKER_ID_LOCK = threading.Lock()
_WORKER_ID_CACHE: dict[str, int] = {}
_IDLE_HEARTBEAT_STOP = threading.Event()
_IDLE_HEARTBEAT_THREAD: threading.Thread | None = None

_DEFAULT_IDLE_HEARTBEAT_INTERVAL_S = 45.0


def executor_class_from_env() -> str:
    """Logical executor pool / worker class advertised on ``workers.worker_type`` (see ``services.worker_pools``)."""
    raw = (os.environ.get("LIVEHOUSE_EXECUTOR_CLASS") or "general").strip()
    return raw if raw else "general"


def _worker_registry_key(worker_name: str, worker_type: str) -> str:
    return f"{worker_type}\0{worker_name}"


def brain_worker_hostname(*, task_self: Any | None = None, worker_sender: Any | None = None) -> str:
    """Stable worker hostname for SSOT worker rows (env overrides Celery request / socket)."""
    env_h = os.environ.get("CELERY_WORKER_HOSTNAME")
    if env_h:
        return str(env_h)
    if task_self is not None:
        req = getattr(task_self, "request", None)
        h = getattr(req, "hostname", None) if req else None
        if h:
            return str(h)
    if worker_sender is not None:
        h = getattr(worker_sender, "hostname", None)
        if h:
            return str(h)
    return socket.gethostname() or "celery-worker"


class WorkerManager:
    """
    Bind DB connection + logical worker identity (``brain@<host>``) for registration and heartbeat.

    When ``explicit_worker_id`` is set (e.g. demo task), ``get_worker_id`` returns it without
    ``register_or_update_worker``; callers should follow the same heartbeat ordering as before.
    """

    def __init__(
        self,
        conn: Any,
        *,
        worker_name: str,
        worker_type: str | None = None,
        explicit_worker_id: int | None = None,
        celery_concurrency: int | None = None,
    ) -> None:
        self._conn = conn
        self._worker_name = worker_name
        self._worker_type = worker_type if worker_type is not None else executor_class_from_env()
        self._explicit_worker_id = explicit_worker_id
        self._celery_concurrency = celery_concurrency

    @classmethod
    def for_celery_task(
        cls,
        conn: Any,
        *,
        task_self: Any | None = None,
        worker_sender: Any | None = None,
        explicit_worker_id: int | None = None,
    ) -> WorkerManager:
        name = f"brain@{brain_worker_hostname(task_self=task_self, worker_sender=worker_sender)}"
        conc = resolve_celery_concurrency(worker_sender)
        return cls(
            conn,
            worker_name=name,
            worker_type=executor_class_from_env(),
            explicit_worker_id=explicit_worker_id,
            celery_concurrency=conc,
        )

    def get_worker_id(self) -> int:
        if self._explicit_worker_id is not None:
            return int(self._explicit_worker_id)
        from utils.luma_brain import register_or_update_worker

        key = _worker_registry_key(self._worker_name, self._worker_type)
        with _WORKER_ID_LOCK:
            cached = _WORKER_ID_CACHE.get(key)
            if cached is not None:
                return cached
            conc = (
                self._celery_concurrency
                if self._celery_concurrency is not None
                else resolve_celery_concurrency(None)
            )
            cap = resolve_advertised_worker_capacity(worker_type=self._worker_type, celery_concurrency=conc)
            wid = register_or_update_worker(
                self._conn,
                worker_name=self._worker_name,
                worker_type=self._worker_type,
                status="ONLINE",
                capacity=cap,
            )
            wid_int = int(wid)
            _WORKER_ID_CACHE[key] = wid_int
            return wid_int

    def heartbeat(self, *, inflight: int | None = None, status: str = "ONLINE") -> None:
        from utils.luma_brain import heartbeat_worker

        kwargs: dict[str, Any] = {
            "conn": self._conn,
            "worker_id": self.get_worker_id(),
            "worker_name": self._worker_name,
            "worker_type": self._worker_type,
            "status": status,
        }
        if inflight is not None:
            kwargs["inflight"] = inflight
        heartbeat_worker(**kwargs)


# Default: well below ``tasks.requeue_stuck_jobs`` ``worker_stale_after_seconds`` (5 min) so
# long jobs keep ``workers.last_heartbeat`` fresh without spamming SQLite.
_DEFAULT_LONG_TASK_HEARTBEAT_INTERVAL_S = 60.0


@contextmanager
def long_task_heartbeat(
    wm: WorkerManager,
    *,
    interval_seconds: float = _DEFAULT_LONG_TASK_HEARTBEAT_INTERVAL_S,
    status: str = "ONLINE",
) -> Generator[None, None, None]:
    """
    While the block runs, refresh ``workers.last_heartbeat`` on a background thread
    (separate DB connection per tick — safe with SQLite + main thread activity).

    Each tick mirrors ``workers.inflight`` from live ``jobs`` pipeline-active counts so dashboards
    stay aligned when Celery concurrency > 1.

    Stops the thread on normal exit, exception, or generator close.
    """
    if interval_seconds <= 0:
        yield
        return

    from utils.luma_brain import brain_connect, count_active_jobs_for_worker, heartbeat_worker

    stop = threading.Event()
    worker_id = wm.get_worker_id()
    wname = wm._worker_name
    wtype = wm._worker_type

    def _loop() -> None:
        while not stop.wait(timeout=interval_seconds):
            conn = brain_connect()
            try:
                live = count_active_jobs_for_worker(conn, worker_id)
                heartbeat_worker(
                    conn,
                    worker_id=worker_id,
                    worker_name=wname,
                    worker_type=wtype,
                    inflight=live,
                    status=status,
                )
            except Exception:
                logger.exception("long_task_heartbeat: heartbeat_worker failed")
            finally:
                conn.close()

    t = threading.Thread(
        target=_loop,
        name="long_task_heartbeat",
        daemon=True,
    )
    t.start()
    try:
        yield
    finally:
        stop.set()
        t.join(timeout=max(5.0, float(interval_seconds) + 2.0))


def _idle_heartbeat_interval_seconds() -> float:
    raw = os.environ.get("LIVEHOUSE_IDLE_HEARTBEAT_INTERVAL_S", "").strip()
    if raw:
        try:
            return max(15.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_IDLE_HEARTBEAT_INTERVAL_S


def _idle_heartbeat_loop(*, worker_sender: Any | None) -> None:
    """Keep ``workers.last_heartbeat`` fresh while the Celery pool process is idle."""
    from utils.luma_brain import brain_connect, count_active_jobs_for_worker, heartbeat_worker

    interval = _idle_heartbeat_interval_seconds()
    conn = brain_connect()
    try:
        wm = WorkerManager.for_celery_task(conn, worker_sender=worker_sender)
        worker_id = wm.get_worker_id()
        wname = wm._worker_name
        wtype = wm._worker_type
    except Exception:
        logger.exception("idle heartbeat: worker registration failed")
        return
    finally:
        conn.close()

    while not _IDLE_HEARTBEAT_STOP.wait(timeout=interval):
        conn = brain_connect()
        try:
            live = count_active_jobs_for_worker(conn, worker_id)
            heartbeat_worker(
                conn,
                worker_id=worker_id,
                worker_name=wname,
                worker_type=wtype,
                inflight=live,
                status="ONLINE",
            )
        except Exception:
            logger.exception("idle heartbeat: heartbeat_worker failed")
        finally:
            conn.close()


def _start_idle_heartbeat_thread(*, worker_sender: Any | None) -> None:
    global _IDLE_HEARTBEAT_THREAD
    _IDLE_HEARTBEAT_STOP.clear()
    if _IDLE_HEARTBEAT_THREAD is not None and _IDLE_HEARTBEAT_THREAD.is_alive():
        return
    _IDLE_HEARTBEAT_THREAD = threading.Thread(
        target=_idle_heartbeat_loop,
        kwargs={"worker_sender": worker_sender},
        name="idle_worker_heartbeat",
        daemon=True,
    )
    _IDLE_HEARTBEAT_THREAD.start()


def _stop_idle_heartbeat_thread() -> None:
    global _IDLE_HEARTBEAT_THREAD
    _IDLE_HEARTBEAT_STOP.set()
    t = _IDLE_HEARTBEAT_THREAD
    if t is not None and t.is_alive():
        t.join(timeout=max(5.0, _idle_heartbeat_interval_seconds() + 2.0))
    _IDLE_HEARTBEAT_THREAD = None


@worker_process_init.connect
def register_brain_worker_on_process_start(sender: Any | None = None, **_kwargs: Any) -> None:
    """Best-effort: register worker once per forked pool process before tasks run."""
    try:
        from utils.luma_brain import brain_connect
    except Exception:
        logger.exception("brain worker warm registration import failed")
        return
    conn = brain_connect()
    try:
        wm = WorkerManager.for_celery_task(conn, worker_sender=sender)
        wm.get_worker_id()
        wm.heartbeat(inflight=0, status="ONLINE")
    except Exception:
        logger.exception("brain worker warm registration failed")
    finally:
        conn.close()
    try:
        _start_idle_heartbeat_thread(worker_sender=sender)
    except Exception:
        logger.exception("idle heartbeat thread start failed")


@worker_process_shutdown.connect
def stop_brain_worker_idle_heartbeat(**_kwargs: Any) -> None:
    """Stop idle heartbeat when a Celery pool child process exits."""
    try:
        _stop_idle_heartbeat_thread()
    except Exception:
        logger.exception("idle heartbeat thread stop failed")
