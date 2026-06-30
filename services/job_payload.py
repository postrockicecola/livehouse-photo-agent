"""Parsing and validation of ``jobs.payload_json`` for pipeline execution."""
from __future__ import annotations

import json
from typing import Any

from celery.utils.log import get_task_logger

from utils.logging_context import make_log_extra

logger = get_task_logger(__name__)


def parse_job_payload(claimed: dict[str, Any], *, job_id: int, trace_id: str | None) -> dict[str, Any]:
    """Parse executor payload from a claimed job row; tolerate bad JSON with structured warnings."""
    raw = claimed.get("payload_json")
    if raw is None or raw == "":
        return {}
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            logger.warning(
                "job payload_json not valid utf-8; ignoring",
                extra=make_log_extra(
                    trace_id=trace_id,
                    job_id=job_id,
                    job_type=claimed.get("job_type"),
                    status="CLAIMED",
                ),
            )
            return {}
    if not isinstance(raw, str):
        logger.warning(
            "job payload_json has unexpected type; ignoring",
            extra=make_log_extra(
                trace_id=trace_id,
                job_id=job_id,
                job_type=claimed.get("job_type"),
                status="CLAIMED",
            ),
        )
        return {}
    try:
        p = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(
            "job payload_json JSON decode failed: %s",
            e,
            extra=make_log_extra(
                trace_id=trace_id,
                job_id=job_id,
                job_type=claimed.get("job_type"),
                status="CLAIMED",
            ),
        )
        return {}
    if not isinstance(p, dict):
        logger.warning(
            "job payload_json must be a JSON object; ignoring",
            extra=make_log_extra(
                trace_id=trace_id,
                job_id=job_id,
                job_type=claimed.get("job_type"),
                status="CLAIMED",
            ),
        )
        return {}
    return p
