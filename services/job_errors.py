"""Exception classification for SSOT job outcomes (permanent vs retryable).

This module answers: **given an exception, should the executor call** ``fail_permanent`` **or**
``fail_retryable``?

It does **not** decide ``DEAD_LETTERED`` — that is driven solely by the job row’s
``attempt`` / ``max_attempts`` budget inside :func:`utils.luma_brain.fail_job_retryable`
(claim counter semantics; see ``utils.luma_brain`` job lifecycle block).
"""
from __future__ import annotations

import json
from typing import Literal


def classify_exception(exc: BaseException) -> Literal["permanent", "retryable"]:
    """
    Map failures to SSOT job outcome hints.

    - ``permanent`` → row should move to ``FAILED_PERMANENT`` (bad input / deterministic error).
    - ``retryable`` → row should move through ``fail_job_retryable`` (may become ``FAILED_RETRYABLE``
      or ``DEAD_LETTERED`` depending on claim budget — not decided here).

    Conservative default: unknown exceptions are ``retryable``.
    """
    if isinstance(
        exc,
        (
            ValueError,
            TypeError,
            FileNotFoundError,
            NotImplementedError,
            json.JSONDecodeError,
            PermissionError,
        ),
    ):
        return "permanent"
    if isinstance(
        exc,
        (
            ConnectionError,
            TimeoutError,
            BrokenPipeError,
            InterruptedError,
        ),
    ):
        return "retryable"
    if isinstance(exc, OSError):
        return "retryable"
    return "retryable"
