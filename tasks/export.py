"""Background Gallery export task with progress updates."""
from __future__ import annotations

from typing import Any

from celery.utils.log import get_task_logger

from celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(name="tasks.prewarm_gallery_export")
def prewarm_gallery_export(
    request_payload: dict[str, Any],
    base_dir: str,
) -> dict[str, Any]:
    from api.gallery_routes import ExportRequest, _export_images_impl

    req = ExportRequest.model_validate(request_payload)
    result = _export_images_impl(req, base_dir=base_dir, prewarm_only=True)
    if not isinstance(result, dict):
        raise RuntimeError("export prewarm returned a non-dict response")
    return result


@celery_app.task(name="tasks.export_gallery_images", bind=True)
def export_gallery_images(
    self,
    request_payload: dict[str, Any],
    base_dir: str,
) -> dict[str, Any]:
    from api.gallery_routes import ExportRequest, _export_images_impl

    req = ExportRequest.model_validate(request_payload)

    def report(done: int, total: int, file_name: str) -> None:
        self.update_state(
            state="PROGRESS",
            meta={
                "done": done,
                "total": total,
                "file": file_name,
                "percent": round(done * 100 / total) if total else 0,
            },
        )

    logger.info("background gallery export start base=%s total=%s", base_dir, len(req.items or req.images))
    result = _export_images_impl(
        req,
        base_dir=base_dir,
        progress_callback=report,
    )
    if not isinstance(result, dict):
        raise RuntimeError("background export returned a non-dict response")
    logger.info(
        "background gallery export done base=%s jpeg=%s raw=%s graded=%s",
        base_dir,
        result.get("count_jpeg"),
        result.get("count_raw"),
        result.get("count_graded_from_raw"),
    )
    return result
