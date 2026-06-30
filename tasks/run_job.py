"""Celery entrypoint for pipeline job execution (SSOT: ``jobs`` table).

**Recommended main path (slice):** ``… → process_brain_ingested → *this module* →
:class:`~services.job_executor.JobExecutor` →
:class:`~services.processor.pipeline_stage_runner.PipelineStageRunner` → inference → artifacts.

**Not** the primary integration surface: ``tasks.run_image_analysis`` (shim),
``AestheticPipeline.run`` / ``run_pipeline.py`` (compatibility CLI / Go mode A subprocess).
"""
from __future__ import annotations

from typing import Any, Dict

from celery_app import celery_app
from services.job_executor import JobExecutor


@celery_app.task(name="tasks.run_job", bind=True)
def run_job(self, job_id: int) -> Dict[str, Any]:
    """
    Stateless executor entrypoint: **only** ``job_id``. Runtime parameters come from ``jobs.payload_json``.

    Business lifecycle is entirely ``jobs.status`` + ``job_events`` (Celery task state is not authoritative).
    """
    return JobExecutor(self).run(job_id)
