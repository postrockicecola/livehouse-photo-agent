"""
Canonical pipeline stage names for stage-aware (PIPELINE_STAGE) jobs.

Legacy monolithic job types ``ANALYZE_SESSION`` / ``ANALYZE_PATH`` run the same stage sequence
in-process via :class:`services.processor.pipeline_stage_runner.PipelineStageRunner` (no Celery chaining).

Stage-aware jobs use :data:`STAGE_JOB_TYPE` with ``stage_name`` set; ``depends_on_job_id`` chains stages.
"""
from __future__ import annotations

# Ordered linear DAG (stage N depends on stage N-1 job row)
CANONICAL_PIPELINE_STAGES: tuple[str, ...] = (
    "PREPARE_INPUT",
    "STAGE1_FILTER",
    "STAGE2_FAST_SCORE",
    "STAGE3_VLM",
    "WRITE_ARTIFACT",
    "FINALIZE",
)

STAGE_JOB_TYPE = "PIPELINE_STAGE"
