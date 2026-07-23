"""
Optional OpenTelemetry TracerProvider + OTLP exporter (Batch E).

Disabled by default. Enable with ``LIVEHOUSE_OTEL=1`` and optionally
``OTEL_EXPORTER_OTLP_ENDPOINT`` (OpenTelemetry env conventions).

Does not install Alertmanager / Grafana — those stay out of scope.
When the SDK is missing, returns a structured skip reason (honest no-op).
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_CONFIGURED = False
_LAST: dict[str, Any] = {"configured": False, "reason": "not_called"}


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
        from opentelemetry.sdk.resources import Resource  # type: ignore[import-not-found]
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-not-found]
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
    _LAST = {
        "configured": True,
        "reason": "ok",
        "service_name": service,
        "exporter": exporter_kind,
        "endpoint_set": bool(endpoint),
    }
    _CONFIGURED = True
    logger.info("otel bootstrap: %s", _LAST)
    return dict(_LAST)


def last_otel_bootstrap_status() -> dict[str, Any]:
    return dict(_LAST)
