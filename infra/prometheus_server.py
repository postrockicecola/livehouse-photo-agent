"""Private Prometheus HTTP endpoint for each Celery worker container."""
from __future__ import annotations

import logging
import os
from typing import Any

from celery.signals import worker_ready, worker_shutdown

logger = logging.getLogger(__name__)
_SERVER: Any | None = None
_THREAD: Any | None = None


def _enabled() -> bool:
    return os.environ.get("LIVEHOUSE_PROMETHEUS_WORKER_SERVER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def start_worker_metrics_server() -> bool:
    """Serve the container's multiprocess registry from the Celery parent."""
    global _SERVER, _THREAD
    if not _enabled() or _SERVER is not None:
        return False
    try:
        from prometheus_client import (
            REGISTRY,
            CollectorRegistry,
            multiprocess,
            start_http_server,
        )

        multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip()
        if multiproc_dir:
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
        else:
            registry = REGISTRY
        port = int(os.environ.get("LIVEHOUSE_METRICS_PORT", "9100"))
        addr = os.environ.get("LIVEHOUSE_METRICS_ADDR", "0.0.0.0")
        _SERVER, _THREAD = start_http_server(port=port, addr=addr, registry=registry)
        logger.info("worker Prometheus endpoint listening on %s:%s", addr, port)
        return True
    except Exception:
        logger.exception("worker Prometheus endpoint failed to start")
        return False


def stop_worker_metrics_server() -> None:
    global _SERVER, _THREAD
    server, thread = _SERVER, _THREAD
    _SERVER = _THREAD = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)


@worker_ready.connect
def _on_worker_ready(**_kwargs: Any) -> None:
    start_worker_metrics_server()


@worker_shutdown.connect
def _on_worker_shutdown(**_kwargs: Any) -> None:
    stop_worker_metrics_server()
