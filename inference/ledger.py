"""
Inference runtime ledger: error taxonomy, per-attempt helpers, and shapes for ``model_runs`` /
``model_run_attempts`` (no DB I/O in this module).
"""
from __future__ import annotations

import re
from typing import Any

# Values stored in ``model_runs.error_type`` and APIs.
ERROR_TIMEOUT = "timeout"
ERROR_TRANSPORT = "transport"
ERROR_PROVIDER = "provider_error"
ERROR_PARSE = "parse"
ERROR_UNKNOWN = "unknown"


def classify_inference_error(
    exc: BaseException | None = None,
    *,
    error_message: str | None = None,
    status_code: int | None = None,
) -> str:
    """
    Coarse, provider-agnostic failure class for platform analytics.

    - *timeout* — read/connect timeouts, deadline exceeded
    - *transport* — DNS, TLS, connection refused, broken pipe
    - *parse* — invalid JSON, schema/shape errors from a successful HTTP read
    - *provider_error* — HTTP 4xx/5xx, model missing, app-level provider errors
    - *unknown* — when nothing else matches
    """
    parts: list[str] = []
    if error_message:
        parts.append(str(error_message))
    if exc is not None:
        parts.append(f"{type(exc).__name__}: {str(exc)}")
    blob = " ".join(parts).lower()
    ex_name = type(exc).__name__ if exc is not None else ""

    if exc is not None and ex_name in ("ReadTimeout", "ConnectTimeout", "Timeout", "TimeoutError"):
        return ERROR_TIMEOUT
    if "timeout" in blob or "timed out" in blob or "deadline" in blob:
        return ERROR_TIMEOUT
    if status_code == 408:
        return ERROR_TIMEOUT

    if exc is not None and ex_name in (
        "ConnectionError",
        "ConnectionRefusedError",
        "gaierror",
        "NameResolutionError",
    ):
        return ERROR_TRANSPORT
    if any(
        x in blob
        for x in (
            "connection refused",
            "connection reset",
            "econnrefused",
            "name or service not known",
            "getaddrinfo",
            "ssl:",
            "certificate",
            "network is unreachable",
            "broken pipe",
        )
    ):
        return ERROR_TRANSPORT
    if status_code in (502, 503, 504):
        return ERROR_TRANSPORT

    if exc is not None and ex_name in ("JSONDecodeError", "ValueError", "TypeError", "KeyError"):
        if "json" in blob or "decode" in blob or "parse" in blob or "expect" in blob:
            return ERROR_PARSE
    if "json" in blob and "decode" in blob:
        return ERROR_PARSE
    if re.search(r"parse|invalid\s+(json|response|format)|unexpected\s+token", blob):
        return ERROR_PARSE

    if status_code is not None and 400 <= status_code < 500 and status_code != 408:
        return ERROR_PROVIDER
    if status_code is not None and status_code >= 500 and status_code not in (502, 503, 504):
        return ERROR_PROVIDER
    if any(
        x in blob
        for x in (
            "model not found",
            "not found",
            "http error",
            "status code",
        )
    ):
        return ERROR_PROVIDER

    return ERROR_UNKNOWN


def build_empty_ledger() -> dict[str, Any]:
    """Shape merged into :class:`inference.types.InferenceResponse` metadata (``inference_ledger``)."""
    return {
        "primary_provider": None,
        "fallback_provider": None,
        "primary_model": None,
        "final_model": None,
        "queue_wait_degraded": False,
        "router_fallback_used": False,
        "primary_latency_ms": None,
        "fallback_hop_latency_ms": None,
        "provider_latency_ms": None,
        "end_to_end_latency_ms": None,
        "error_type": None,
        "attempts": [],
    }


def append_inference_attempt(
    ledger: dict[str, Any],
    *,
    role: str,
    provider_id: str,
    model_name: str,
    latency_ms: int,
    ok: bool,
    error_type: str | None = None,
    error_message: str | None = None,
    primary_skipped: bool = False,
) -> None:
    """Append one provider hop to in-memory ledger (later persisted to ``model_run_attempts``)."""
    attempts = ledger.setdefault("attempts", [])
    msg = (error_message or "").strip()
    attempts.append(
        {
            "role": role,
            "provider_id": (provider_id or "").strip() or "unknown",
            "model_name": (model_name or "") or None,
            "latency_ms": max(0, int(latency_ms)),
            "ok": bool(ok),
            "error_type": error_type,
            "error_message": (msg[:500] if msg else None),
            "primary_skipped": bool(primary_skipped),
        }
    )


def compute_outcome_attribution(*, ledger: dict[str, Any], payload_status: str) -> str | None:
    """
    Run-level outcome label from ordered attempts and final payload status.

    - *primary_success* — succeeded on the primary hop only
    - *fallback_success* — succeeded after fallback or queue-forced single fallback hop
    - *primary_failed* — primary failed, no second hop was made
    - *all_failed* — primary then fallback both failed
    - *fallback_only_failed* — only a fallback hop ran (e.g. queue forcing) and it failed
    - *exception* — router/ledger did not record hops (e.g. unexpected exception)
    - *unknown_success* — success but no attempt rows (should not happen in normal router flow)
    """
    attempts: list[dict[str, Any]] = list(ledger.get("attempts") or [])
    st = str(payload_status or "").upper()
    success = st == "SUCCESS"

    if not attempts:
        return "unknown_success" if success else "exception"

    if success:
        ok_idx = [i for i, a in enumerate(attempts) if a.get("ok")]
        if not ok_idx:
            return "unknown_success"
        i = ok_idx[-1]
        if i == 0 and attempts[0].get("role") == "primary":
            return "primary_success"
        return "fallback_success"

    if len(attempts) == 1:
        if attempts[0].get("role") == "fallback" or attempts[0].get("primary_skipped"):
            return "fallback_only_failed"
        return "primary_failed"
    return "all_failed"
