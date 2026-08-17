"""Celery worker for Gallery async curation jobs."""
from __future__ import annotations

from typing import Any

from celery_app import celery_app


@celery_app.task(name="tasks.run_curation_job")
def run_curation_job_task(job_id: str) -> dict[str, Any]:
    from services.agent.curation_jobs import run_curation_job

    return run_curation_job(job_id)
