"""Shared helpers for creating ANALYZE_* jobs and dispatching ``tasks.run_job``.

Keeps gallery ``/api/tasks/analyze`` and studio ``/api/studio/analyze`` payload
defaults from drifting further apart.
"""
from __future__ import annotations

from typing import Any, Optional


def build_analyze_job_payload(
    *,
    config_path: str,
    source_dir: str,
    enable_checkpoint: bool = True,
    force_full_rerun: bool = False,
    max_workers: Optional[int] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "config_path": config_path,
        "source_dir": source_dir,
        "enable_checkpoint": bool(enable_checkpoint),
        "force_full_rerun": bool(force_full_rerun),
    }
    if max_workers is not None:
        payload["max_workers"] = int(max_workers)
    return payload
