"""Single place to configure process-wide logging with stable structured fields.

Env:
  LIVEHOUSE_LOG_JSON=1 — one JSON object per line (good for prod / log agents)
  LIVEHOUSE_LOG_LEVEL=INFO|DEBUG|...
  LIVEHOUSE_LOG_FORCE=1 — replace existing root handlers (tests / special entrypoints)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, TextIO

# Canonical dimensions for AI-infra correlation (JSON logs: all keys; text logs: only set keys).
STRUCTURED_KEYS: tuple[str, ...] = (
    "trace_id",
    "job_id",
    "session_id",
    "photo_id",
    "worker_id",
    "provider",
    "model",
    "image_trace_id",
    "status",
    "latency_ms",
    "queue_wait_ms",
    "error_code",
    "namespace",
    "project_key",
    # Inference queue / batch infra (optional on most records).
    "batch_id",
    "batch_size",
    "queue_size",
    "inflight",
    "pending",
    "throughput_img_per_sec",
    "p95_queue_wait_ms",
    "inference_per_sec",
    "gpu_util",
    "avg_latency_ms",
    "p95_latency_ms",
    "gpu_busy_sec",
    "cache_hit_rate",
    "total_processed",
)

_ATTACHED_FILES: set[str] = set()


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name, "").strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def _coerce_field(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key == "job_id" and value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key in {
        "session_id",
        "photo_id",
        "worker_id",
        "latency_ms",
        "queue_wait_ms",
        "batch_size",
        "queue_size",
        "inflight",
        "pending",
        "p95_queue_wait_ms",
        "avg_latency_ms",
        "p95_latency_ms",
        "total_processed",
    } and value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if key in {"throughput_img_per_sec", "inference_per_sec", "gpu_util", "cache_hit_rate", "gpu_busy_sec"} and value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    return value


def structured_values(record: logging.LogRecord) -> dict[str, Any]:
    return {k: _coerce_field(k, getattr(record, k, None)) for k in STRUCTURED_KEYS}


class StructuredTextFormatter(logging.Formatter):
    """Human-readable line; optional key=value tail for fields that were set."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        tail_bits = []
        for k, v in structured_values(record).items():
            if v is None:
                continue
            tail_bits.append(f"{k}={v}")
        if not tail_bits:
            return base
        return f"{base} | " + " ".join(tail_bits)


class JsonLineFormatter(logging.Formatter):
    """One JSON object per line; includes mandatory keys (null if absent)."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        payload.update(structured_values(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except TypeError:
            payload["msg"] = str(record.msg)
            return json.dumps(payload, ensure_ascii=False, default=str)


def create_formatter(*, json_lines: bool) -> logging.Formatter:
    if json_lines:
        return JsonLineFormatter()
    return StructuredTextFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def configure_logging(
    *,
    level: int | str | None = None,
    json_lines: bool | None = None,
    stream: TextIO | None = None,
    force: bool | None = None,
) -> None:
    """Attach a single StreamHandler to the root logger with structured formatting."""
    if json_lines is None:
        json_lines = _env_bool("LIVEHOUSE_LOG_JSON")
    if force is None:
        force = _env_bool("LIVEHOUSE_LOG_FORCE")

    root = logging.getLogger()
    if level is None:
        level = os.environ.get("LIVEHOUSE_LOG_LEVEL", "INFO").upper()
    if isinstance(level, str):
        level = getattr(logging, level, logging.INFO)

    if root.handlers and not force:
        root.setLevel(min(root.level, int(level)) if root.level else int(level))
        return

    if force and root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(create_formatter(json_lines=json_lines))
    root.addHandler(handler)
    root.setLevel(int(level))


def attach_file_handler(path: str, *, json_lines: bool | None = None, encoding: str = "utf-8") -> None:
    """Append a file handler with the same formatter (deduped by path)."""
    ap = os.path.abspath(path)
    if ap in _ATTACHED_FILES:
        return
    if json_lines is None:
        json_lines = _env_bool("LIVEHOUSE_LOG_JSON")
    fh = logging.FileHandler(path, encoding=encoding)
    fh.setFormatter(create_formatter(json_lines=json_lines))
    logging.getLogger().addHandler(fh)
    _ATTACHED_FILES.add(ap)
