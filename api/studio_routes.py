"""Studio API: sessions list, active session pointer, analyze trigger, status."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from celery import Celery
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from utils.logging_context import new_trace_id
from utils.luma_brain import brain_connect, create_analyze_path_job, create_job
from utils.runtime_session import write_latest_session_pointer
from utils.studio_sessions import (
    active_session_from_archive,
    analysis_results_ready,
    find_brain_session_id,
    find_runnable_analyze_job_id,
    job_elapsed_seconds,
    latest_job_for_previews,
    list_studio_sessions,
    list_recent_deliveries,
    pipeline_view_from_job,
    pipeline_view_with_stages,
    resolve_default_archive_root,
    session_activity_label,
)

router = APIRouter(tags=["studio"])

_CELERY_BROKER = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
_CELERY_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
_celery = Celery("livehouse_studio", broker=_CELERY_BROKER, backend=_CELERY_BACKEND)

_RUNNING = frozenset(
    {"QUEUED", "CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING", "FAILED_RETRYABLE"}
)


def _gallery_base_dir() -> str:
    from api.gallery_routes import BASE_DIR

    return BASE_DIR


def _clear_gallery_runtime_cache() -> None:
    from api import gallery_routes

    gallery_routes._gallery_active_dir_cache = None  # noqa: SLF001


def _archive_root(explicit: str | None = None) -> Path:
    if explicit and str(explicit).strip():
        p = Path(explicit).expanduser()
        if not p.is_dir():
            raise HTTPException(status_code=400, detail=f"archive_root is not a directory: {explicit}")
        return p.resolve()

    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()

    override = (os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR") or "").strip()
    source_hint = override or _gallery_base_dir()
    if not override:
        from utils.studio_sessions import read_source_dir_from_yaml

        cfg_sd = read_source_dir_from_yaml()
        if not cfg_sd:
            try:
                from utils.config_loader import ConfigLoader

                cfg = ConfigLoader.load()
                cfg_sd = (cfg.get("paths") or {}).get("source_dir")
            except Exception:
                cfg_sd = None
        if cfg_sd:
            source_hint = str(cfg_sd)
    return resolve_default_archive_root(source_hint)


class ActiveSessionBody(BaseModel):
    previews_dir: str = Field(..., min_length=1)


class AnalyzeBody(BaseModel):
    previews_dir: str = Field(..., min_length=1)
    config_path: str = Field(default="configs/livehouse.yaml")
    # Studio Analyze defaults to a full refresh (clear audit + re-run all frames).
    force_full_rerun: bool = True
    enable_checkpoint: bool | None = None


class IngestConfigPutBody(BaseModel):
    ingest_monitor_path: str | None = None
    archive_root: str | None = None
    session_folder_name: str | None = None


@router.get("/api/studio/ingest-config")
def studio_get_ingest_config():
    from utils.studio_ingest_config import read_ingest_config

    return read_ingest_config()


@router.put("/api/studio/ingest-config")
def studio_put_ingest_config(body: IngestConfigPutBody):
    from utils.studio_ingest_config import save_ingest_config

    try:
        return save_ingest_config(
            ingest_monitor_path=body.ingest_monitor_path,
            archive_root=body.archive_root,
            session_folder_name=body.session_folder_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"failed to write ingest config: {e}") from e


@router.get("/api/studio/sessions")
def studio_list_sessions(
    archive_root: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=500),
):
    ar = _archive_root(archive_root)
    conn = None
    try:
        conn = brain_connect()
    except Exception:
        conn = None
    try:
        items = list_studio_sessions(conn, ar, limit=limit)
        active = active_session_from_archive(ar)
        return {
            "archive_root": str(ar),
            "active": active,
            "sessions": items,
            "count": len(items),
            "recent_deliveries": list_recent_deliveries(items, limit=8),
        }
    finally:
        if conn is not None:
            conn.close()


@router.get("/api/studio/status")
def studio_status(
    archive_root: str | None = Query(default=None),
    previews_dir: str | None = Query(default=None),
):
    ar = _archive_root(archive_root)
    active = active_session_from_archive(ar)
    target_previews = (previews_dir or "").strip()
    if not target_previews and active:
        target_previews = str(active.get("previews_dir") or "")
    if not target_previews:
        return {
            "archive_root": str(ar),
            "active": active,
            "session": None,
            "job": None,
            "pipeline": pipeline_view_from_job(None, []),
            "events": [],
        }

    previews_path = Path(target_previews).expanduser().resolve()
    if not previews_path.is_dir():
        raise HTTPException(status_code=400, detail=f"previews_dir not found: {target_previews}")

    session_key = previews_path.parent.name
    has_results = analysis_results_ready(previews_path)
    session_summary = {
        "session_key": session_key,
        "session_dir": str(previews_path.parent.resolve()),
        "previews_dir": str(previews_path),
        "preview_count": sum(
            1
            for ent in previews_path.iterdir()
            if ent.is_file() and ent.suffix.lower() in {".jpg", ".jpeg"}
        ),
        "has_analysis_results": has_results,
    }

    job: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    brain_session_id: int | None = None
    try:
        conn = brain_connect()
        try:
            brain_session_id = find_brain_session_id(conn, str(previews_path))
            job, events = latest_job_for_previews(
                conn,
                previews_dir=str(previews_path),
                brain_session_id=brain_session_id,
            )
        finally:
            conn.close()
    except Exception:
        pass

    pipeline = pipeline_view_with_stages(
        job,
        events,
        preview_count=int(session_summary.get("preview_count") or 0),
        previews_dir=previews_path,
    )
    activity = session_activity_label(job, has_analysis_results=has_results)

    job_public = None
    if job:
        job_public = {
            "id": int(job["id"]),
            "job_type": str(job.get("job_type") or ""),
            "status": str(job.get("status") or ""),
            "trace_id": str(job.get("trace_id") or ""),
            "elapsed_sec": job_elapsed_seconds(job),
            "is_running": str(job.get("status") or "") in _RUNNING,
        }

    return {
        "archive_root": str(ar),
        "active": active,
        "session": {**session_summary, "brain_session_id": brain_session_id, "activity": activity},
        "job": job_public,
        "pipeline": pipeline,
        "events": [
            {
                "id": int(e["id"]),
                "to_status": e.get("to_status"),
                "message": e.get("message"),
                "created_at": int(e["created_at"]) if e.get("created_at") else None,
                "payload_json": e.get("payload_json"),
            }
            for e in events
        ],
    }


@router.put("/api/studio/active-session")
def studio_set_active_session(body: ActiveSessionBody):
    previews = Path(body.previews_dir).expanduser().resolve()
    if not previews.is_dir():
        raise HTTPException(status_code=400, detail=f"previews_dir is not a directory: {body.previews_dir}")
    if previews.name.lower() != "previews":
        raise HTTPException(status_code=400, detail="path must be a Previews directory")

    written = write_latest_session_pointer(previews)
    if written is None:
        raise HTTPException(status_code=500, detail="failed to write latest_session.json")
    _clear_gallery_runtime_cache()

    ar = previews.parent.parent
    return {
        "ok": True,
        "latest_session_path": str(written),
        "active": active_session_from_archive(ar),
    }


@router.post("/api/studio/analyze")
def studio_start_analyze(body: AnalyzeBody):
    previews = Path(body.previews_dir).expanduser().resolve()
    if not previews.is_dir():
        raise HTTPException(status_code=400, detail=f"previews_dir is not a directory: {body.previews_dir}")

    trace_id = new_trace_id("studio_analyze")
    config_path = body.config_path or os.getenv("LIVEHOUSE_CONFIG", "configs/livehouse.yaml")
    force_full = bool(body.force_full_rerun)
    enable_checkpoint = False if force_full else (
        True if body.enable_checkpoint is None else bool(body.enable_checkpoint)
    )
    job_payload: dict = {
        "config_path": config_path,
        "enable_checkpoint": enable_checkpoint,
        "force_full_rerun": force_full,
        "source_dir": str(previews),
    }

    conn = brain_connect()
    try:
        sid = find_brain_session_id(conn, str(previews))
        existing_id = find_runnable_analyze_job_id(
            conn,
            previews_dir=str(previews),
            brain_session_id=sid,
        )
        if existing_id is not None:
            return {
                "ok": True,
                "job_id": existing_id,
                "status": "already_running",
                "trace_id": trace_id,
                "message": "analysis already queued or running for this session",
            }
        if sid is not None:
            job_id = create_job(
                conn,
                job_type="ANALYZE_SESSION",
                session_id=sid,
                trace_id=trace_id,
                payload=job_payload,
            )
        else:
            job_id = create_analyze_path_job(
                conn,
                source_dir=str(previews),
                config_path=config_path,
                enable_checkpoint=enable_checkpoint,
                force_full_rerun=force_full,
                trace_id=trace_id,
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"create_job failed: {e}") from e
    finally:
        conn.close()

    task = _celery.send_task("tasks.run_job", args=[job_id])
    write_latest_session_pointer(previews)
    _clear_gallery_runtime_cache()

    return {
        "ok": True,
        "job_id": job_id,
        "status": "QUEUED",
        "trace_id": trace_id,
        "run_task_id": task.id,
        "task_name": "tasks.run_job",
        "previews_dir": str(previews),
        "force_full_rerun": force_full,
        "enable_checkpoint": enable_checkpoint,
    }
