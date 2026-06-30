"""
Celery task package.

Import submodules for side effects so ``@celery_app.task`` registrations run.
Task names remain ``tasks.<name>`` for broker/beat compatibility.

**Current main execution entry:** ``tasks.run_job`` → ``services.job_executor.JobExecutor``
(``tasks/run_job.py``). Ingest seeding/dispatch: ``tasks/ingest.py`` (``process_brain_ingested``).

**Legacy / shims:** ``tasks/misc.py`` (deprecated ``run_image_analysis``, demos, optional workflows).
"""
from __future__ import annotations

from . import film_prewarm as _film_prewarm  # noqa: F401
from . import ingest as _ingest  # noqa: F401
from . import maintenance as _maintenance  # noqa: F401
from . import misc as _misc  # noqa: F401  # start_staged_session_pipeline, etc.
from .run_job import run_job as run_job  # noqa: F401

__all__ = ["run_job"]
