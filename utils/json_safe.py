"""Recursively convert numpy / other non-JSON types for json.dumps."""
from __future__ import annotations

from typing import Any


def json_safe(obj: Any) -> Any:
    """Return a structure that json.dumps can serialize (numpy scalars → Python)."""
    try:
        import numpy as np
    except ImportError:
        np = None  # type: ignore

    if np is not None:
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, np.generic):
            return obj.item()

    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    return obj
