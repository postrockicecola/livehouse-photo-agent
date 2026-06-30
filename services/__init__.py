"""Livehouse job orchestration and pipeline services (production core).

**Orchestration:** :mod:`services.job_executor`, :mod:`services.job_lifecycle`,
dispatch helpers (:mod:`services.job_dispatch`), scheduler policy (:mod:`services.scheduler`).

**Pipeline (main):** :mod:`services.processor.pipeline_stage_runner` ← ``JobExecutor`` ← ``tasks.run_job``.

**Compatibility:** :class:`services.processor.aesthetic_pipeline.AestheticPipeline` / ``run_pipeline.py``.

**Not in this package:** HTTP apps live at ``gallery_server.py`` / ``api/``;
Celery task envelopes in ``tasks/``; SQLite access patterns in ``utils.luma_brain``.
See ``docs/REPO_STRUCTURE.md`` for core vs legacy boundaries.
"""
