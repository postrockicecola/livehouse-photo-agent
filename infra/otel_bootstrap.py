"""
Optional OpenTelemetry TracerProvider + OTLP exporter (Batch E).

Disabled by default. Enable with ``LIVEHOUSE_OTEL=1`` and optionally
``OTEL_EXPORTER_OTLP_ENDPOINT`` (OpenTelemetry env conventions).

The optional Compose observability profile supplies Collector, Tempo, Prometheus,
Alertmanager, and Grafana; this module remains safe for non-Compose runtimes.
When the SDK is missing, returns a structured skip reason (honest no-op).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_CONFIGURED = False
_LAST: dict[str, Any] = {"configured": False, "reason": "not_called"}
_REQUESTS_INSTRUMENTED = False
_CELERY_INSTRUMENTED = False
_FASTAPI_APP_IDS: set[int] = set()


def otel_enabled_from_env() -> bool:
    return os.environ.get("LIVEHOUSE_OTEL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def configure_otel_from_env(*, force: bool = False) -> dict[str, Any]:
    """Idempotent process bootstrap. Safe to call from API and Celery workers."""
    global _CONFIGURED, _LAST
    if _CONFIGURED and not force:
        return dict(_LAST)
    if not otel_enabled_from_env():
        _LAST = {"configured": False, "reason": "disabled"}
        _CONFIGURED = True
        return dict(_LAST)
    try:
        from opentelemetry import trace  # type: ignore[import-not-found]
        from opentelemetry.sdk.resources import (
            Resource,  # type: ignore[import-not-found]
        )
        from opentelemetry.sdk.trace import (
            TracerProvider,  # type: ignore[import-not-found]
        )
        from opentelemetry.sdk.trace.export import (  # type: ignore[import-not-found]
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except Exception as exc:
        _LAST = {"configured": False, "reason": "sdk_missing", "error": str(exc)[:200]}
        _CONFIGURED = True
        logger.info("otel bootstrap skipped: %s", _LAST["reason"])
        return dict(_LAST)

    service = (os.environ.get("OTEL_SERVICE_NAME") or "livehouse").strip() or "livehouse"
    resource = Resource.create({"service.name": service})
    provider = TracerProvider(resource=resource)
    endpoint = (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    exporter: Any
    exporter_kind = "console"
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[import-not-found]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter()
            exporter_kind = "otlp_http"
        except Exception as exc:
            logger.warning("OTLP exporter unavailable (%s); using console exporter", exc)
            exporter = ConsoleSpanExporter()
            exporter_kind = "console_fallback"
    else:
        exporter = ConsoleSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    instrumented: list[str] = []
    global _REQUESTS_INSTRUMENTED
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        if not _REQUESTS_INSTRUMENTED:
            RequestsInstrumentor().instrument(tracer_provider=provider)
            _REQUESTS_INSTRUMENTED = True
        instrumented.append("requests")
    except Exception as exc:
        logger.warning("OTEL requests instrumentation unavailable: %s", exc)
    _LAST = {
        "configured": True,
        "reason": "ok",
        "service_name": service,
        "exporter": exporter_kind,
        "endpoint_set": bool(endpoint),
        "instrumented": instrumented,
    }
    _CONFIGURED = True
    logger.info("otel bootstrap: %s", _LAST)
    return dict(_LAST)


def last_otel_bootstrap_status() -> dict[str, Any]:
    return dict(_LAST)


def instrument_fastapi_app(app: Any) -> bool:
    """Instrument one FastAPI app after construction; safe when OTEL is disabled."""
    if not _LAST.get("configured") or id(app) in _FASTAPI_APP_IDS:
        return False
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        _FASTAPI_APP_IDS.add(id(app))
        _LAST.setdefault("instrumented", []).append("fastapi")
        return True
    except Exception as exc:
        logger.warning("OTEL FastAPI instrumentation unavailable: %s", exc)
        return False


def instrument_celery() -> bool:
    """Install Celery producer/consumer tracing and W3C context propagation."""
    global _CELERY_INSTRUMENTED
    if not _LAST.get("configured") or _CELERY_INSTRUMENTED:
        return False
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()
        _CELERY_INSTRUMENTED = True
        _LAST.setdefault("instrumented", []).append("celery")
        return True
    except Exception as exc:
        logger.warning("OTEL Celery instrumentation unavailable: %s", exc)
        return False


def set_current_span_attributes(**attributes: Any) -> None:
    """Attach business identifiers to the active HTTP/Celery/job span."""
    if not _LAST.get("configured"):
        return
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(str(key), value)
    except Exception:
        pass


@contextmanager
def otel_span(name: str, **attributes: Any):
    """Best-effort current span used by core business boundaries."""
    if not _LAST.get("configured"):
        yield None
        return
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("livehouse.core")
        attrs = {str(k): v for k, v in attributes.items() if v is not None}
    except Exception:
        yield None
        return
    with tracer.start_as_current_span(name, attributes=attrs) as span:
        yield span
