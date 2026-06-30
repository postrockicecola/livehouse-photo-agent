"""Celery: warm ``film_render_cache`` for homepage Cinestill gallery thumbs."""
from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from celery_app import celery_app
from services.gallery_film_prewarm import run_gallery_cinestill_prewarm

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.prewarm_gallery_cinestill", bind=True)
def prewarm_gallery_cinestill(self, previews_base_dir: str) -> dict[str, Any]:
    """
    After analysis, pre-render ``film_cinestill_800t`` at gallery thumb size so the web UI
  hits disk cache instead of cold ``op_kernel`` on scroll.
    """
    logger.info("tasks.prewarm_gallery_cinestill start base=%s", previews_base_dir)
    out = run_gallery_cinestill_prewarm(previews_base_dir)
    logger.info("tasks.prewarm_gallery_cinestill done %s", out)
    return out
