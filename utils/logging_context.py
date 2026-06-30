"""Lightweight helpers for structured logging context."""
from __future__ import annotations

import uuid
from typing import Any

from utils.logging_setup import STRUCTURED_KEYS

__all__ = ["STRUCTURED_KEYS", "new_trace_id", "make_log_extra"]


def new_trace_id(prefix: str = "trace") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def make_log_extra(**kwargs: Any) -> dict[str, Any]:
    """Build logging ``extra`` with optional canonical keys (see STRUCTURED_KEYS).

    None values are dropped. Text formatters only append set fields; JSON lines
    still include all STRUCTURED_KEYS (null if absent).
    """
    return {k: v for k, v in kwargs.items() if v is not None}
