"""Infrastructure helpers shared by Celery tasks, FastAPI, and inference.

Production-oriented modules: worker registration/heartbeat (:mod:`infra.worker_manager`),
capacity hints (:mod:`infra.worker_capacity`), aggregated metrics (:mod:`infra.metrics`),
combined health probes (:mod:`infra.health`). Consumed by ``gallery_server``,
``services.job_executor``, and optionally ``inference.router``.

Boundary notes: ``docs/REPO_STRUCTURE.md``.
"""
