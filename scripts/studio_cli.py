#!/usr/bin/env python3
"""Studio JSON helpers for Next.js API routes (stdout only)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


def _archive_root(explicit: str | None = None) -> Path:
    from utils.studio_sessions import resolve_default_archive_root

    if explicit and str(explicit).strip():
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p.resolve()

    env = (os.environ.get("LUMA_ARCHIVE_ROOT") or "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_dir():
            return p.resolve()

    override = (os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR") or "").strip()
    if override:
        return resolve_default_archive_root(override)

    from utils.studio_sessions import read_source_dir_from_yaml

    source_dir = read_source_dir_from_yaml() or "."
    try:
        from utils.config_loader import ConfigLoader

        cfg = ConfigLoader.load()
        source_dir = (cfg.get("paths") or {}).get("source_dir") or source_dir
    except Exception:
        pass

    try:
        from api.gallery_routes import BASE_DIR

        if BASE_DIR and Path(BASE_DIR).is_dir():
            source_dir = BASE_DIR
    except Exception:
        pass

    return resolve_default_archive_root(source_dir)


def cmd_landing_gallery(export_dir: str, count: int) -> dict:
    import random

    root = Path(export_dir).expanduser().resolve()
    if not root.is_dir():
        return {"export_dir": str(root), "images": [], "error": "export_dir not found"}

    exts = {".jpg", ".jpeg", ".png", ".webp"}
    candidates: list[str] = []
    try:
        for ent in root.rglob("*"):
            if ent.is_file() and ent.suffix.lower() in exts:
                candidates.append(str(ent.resolve()))
    except OSError:
        candidates = []

    if not candidates:
        return {"export_dir": str(root), "images": []}

    n = max(1, min(int(count), len(candidates)))
    picked = random.sample(candidates, n)
    return {
        "export_dir": str(root),
        "images": [{"path": p} for p in picked],
    }


def cmd_landing_brain() -> dict:
    from utils.luma_brain import brain_connect

    table_keys = (
        ("jobs", "jobs"),
        ("job_events", "events"),
        ("artifacts", "artifacts"),
        ("sessions", "sessions"),
        ("photos", "photos"),
        ("infra_runtime_snapshots", "snapshots"),
    )
    counts: dict[str, int] = {key: 0 for _, key in table_keys}
    trace: list[dict] = []

    conn = None
    try:
        conn = brain_connect()
    except Exception:
        conn = None

    if conn is not None:
        try:
            for table, key in table_keys:
                try:
                    row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                    counts[key] = int(row[0]) if row else 0
                except Exception:
                    counts[key] = 0

            rows = conn.execute(
                """
                SELECT id, job_id, from_status, to_status, created_at
                FROM job_events
                ORDER BY created_at DESC
                LIMIT 6
                """
            ).fetchall()
            for r in rows:
                trace.append(
                    {
                        "id": int(r[0]),
                        "job_id": int(r[1]) if r[1] is not None else None,
                        "from_status": str(r[2] or ""),
                        "to_status": str(r[3] or ""),
                        "created_at": int(r[4]) if r[4] is not None else None,
                    }
                )
        finally:
            conn.close()

    return {"counts": counts, "trace": trace}


def cmd_landing_infra() -> dict:
    from utils.luma_brain import brain_connect

    metrics: dict[str, int] = {
        "queue_depth": 0,
        "workers_online": 0,
        "workers_total": 0,
        "retry_pending": 0,
        "recovery_requeues": 0,
        "pipeline_active": 0,
        "monitoring_snapshots": 0,
        "dead_letter": 0,
    }
    flow: list[dict] = []

    conn = None
    try:
        conn = brain_connect()
    except Exception:
        conn = None

    if conn is not None:
        try:
            active_statuses = (
                "CLAIMED",
                "PREPROCESSING",
                "INFERENCING",
                "POSTPROCESSING",
            )
            for status, key in (
                ("QUEUED", "queue_depth"),
                ("FAILED_RETRYABLE", "retry_pending"),
                ("DEAD_LETTERED", "dead_letter"),
            ):
                try:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM jobs WHERE status = ?", (status,)
                    ).fetchone()
                    metrics[key] = int(row[0]) if row else 0
                except Exception:
                    pass

            try:
                placeholders = ",".join("?" for _ in active_statuses)
                row = conn.execute(
                    f"SELECT COUNT(*) FROM jobs WHERE status IN ({placeholders})",
                    active_statuses,
                ).fetchone()
                metrics["pipeline_active"] = int(row[0]) if row else 0
            except Exception:
                pass

            try:
                row = conn.execute("SELECT COUNT(*) FROM workers").fetchone()
                metrics["workers_total"] = int(row[0]) if row else 0
                row = conn.execute(
                    "SELECT COUNT(*) FROM workers WHERE status = 'ONLINE'"
                ).fetchone()
                metrics["workers_online"] = int(row[0]) if row else 0
            except Exception:
                pass

            try:
                row = conn.execute("SELECT COUNT(*) FROM infra_runtime_snapshots").fetchone()
                metrics["monitoring_snapshots"] = int(row[0]) if row else 0
            except Exception:
                pass

            try:
                row = conn.execute(
                    """
                    SELECT COUNT(*) FROM job_events
                    WHERE from_status = 'FAILED_RETRYABLE' AND to_status = 'QUEUED'
                    """
                ).fetchone()
                metrics["recovery_requeues"] = int(row[0]) if row else 0
            except Exception:
                pass

            rows = conn.execute(
                """
                SELECT id, job_id, from_status, to_status, created_at
                FROM job_events
                WHERE to_status IN ('FAILED_RETRYABLE', 'QUEUED', 'CLAIMED', 'SUCCEEDED')
                   OR from_status IN ('FAILED_RETRYABLE', 'DEAD_LETTERED')
                ORDER BY created_at DESC
                LIMIT 5
                """
            ).fetchall()
            for r in rows:
                flow.append(
                    {
                        "id": int(r[0]),
                        "job_id": int(r[1]) if r[1] is not None else None,
                        "from_status": str(r[2] or ""),
                        "to_status": str(r[3] or ""),
                        "created_at": int(r[4]) if r[4] is not None else None,
                    }
                )
        finally:
            conn.close()

    return {"metrics": metrics, "flow": flow}


def _probe_redis_status() -> str:
    try:
        import redis

        url = (
            os.environ.get("CELERY_BROKER_URL")
            or os.environ.get("REDIS_URL")
            or "redis://localhost:6379/0"
        )
        client = redis.from_url(url, socket_connect_timeout=1.5, socket_timeout=1.5)
        client.ping()
        return "online"
    except Exception:
        return "offline"


def cmd_infra_overview() -> dict:
    """Studio workbench: queue/workers + lifetime job stats + Redis/Brain health."""
    from utils.luma_brain import brain_connect

    landing = cmd_landing_infra()
    metrics = landing.get("metrics") if isinstance(landing.get("metrics"), dict) else {}

    jobs_processed = 0
    average_latency_ms: int | None = None
    pipeline_success_rate_pct: int | None = None
    database_status = "offline"

    conn = None
    try:
        conn = brain_connect()
        database_status = "online"
        try:
            row = conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'SUCCEEDED'").fetchone()
            jobs_processed = int(row[0]) if row else 0
        except Exception:
            pass
        try:
            row = conn.execute(
                """
                SELECT AVG(total_latency_ms)
                FROM jobs
                WHERE status = 'SUCCEEDED'
                  AND total_latency_ms IS NOT NULL
                  AND total_latency_ms > 0
                """
            ).fetchone()
            if row and row[0] is not None:
                average_latency_ms = max(0, int(round(float(row[0]))))
        except Exception:
            pass
        try:
            succ = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'SUCCEEDED'").fetchone()[0])
            failed = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM jobs
                    WHERE status IN ('FAILED_PERMANENT', 'DEAD_LETTERED', 'CANCELLED')
                    """
                ).fetchone()[0]
            )
            denom = succ + failed
            if denom > 0:
                pipeline_success_rate_pct = int(round(100.0 * succ / denom))
        except Exception:
            pass
    except Exception:
        database_status = "offline"
    finally:
        if conn is not None:
            conn.close()

    return {
        "workers_online": int(metrics.get("workers_online") or 0),
        "workers_total": int(metrics.get("workers_total") or 0),
        "queue_depth": int(metrics.get("queue_depth") or 0),
        "pipeline_active": int(metrics.get("pipeline_active") or 0),
        "jobs_processed": jobs_processed,
        "average_latency_ms": average_latency_ms,
        "pipeline_success_rate_pct": pipeline_success_rate_pct,
        "redis_status": _probe_redis_status(),
        "database_status": database_status,
    }


def cmd_stats() -> dict:
    from utils.luma_brain import brain_connect
    from utils.studio_sessions import collect_lifetime_stats

    ar = _archive_root()
    conn = None
    try:
        conn = brain_connect()
    except Exception:
        conn = None
    try:
        stats = collect_lifetime_stats(conn, ar)
    finally:
        if conn is not None:
            conn.close()
    return {"archive_root": str(ar), **stats}


def cmd_sessions(limit: int) -> dict:
    from utils.studio_sessions import active_session_from_archive, list_recent_deliveries, list_studio_sessions
    from utils.luma_brain import brain_connect

    ar = _archive_root()
    conn = None
    try:
        conn = brain_connect()
    except Exception:
        conn = None
    try:
        items = list_studio_sessions(conn, ar, limit=limit)
    finally:
        if conn is not None:
            conn.close()

    return {
        "archive_root": str(ar),
        "active": active_session_from_archive(ar),
        "sessions": items,
        "count": len(items),
        "recent_deliveries": list_recent_deliveries(items, limit=8),
    }


def cmd_featured_frames(previews_dir: str) -> dict:
    from utils.studio_sessions import featured_frames_for_session

    previews_path = Path(previews_dir).expanduser().resolve()
    if not previews_path.is_dir():
        raise SystemExit(json.dumps({"error": f"previews_dir not found: {previews_dir}"}))
    frames = featured_frames_for_session(previews_path)
    return {
        "previews_dir": str(previews_path),
        "frames": frames,
        "count": len(frames),
    }


def cmd_status(previews_dir: str) -> dict:
    from utils.studio_sessions import (
        active_session_from_archive,
        analysis_results_ready,
        find_brain_session_id,
        job_elapsed_seconds,
        latest_job_for_previews,
        pipeline_view_from_job,
        pipeline_view_with_stages,
        session_activity_label,
    )
    from utils.luma_brain import brain_connect

    ar = _archive_root()
    previews_path = Path(previews_dir).expanduser().resolve()
    if not previews_path.is_dir():
        raise SystemExit(json.dumps({"error": f"previews_dir not found: {previews_dir}"}))

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

    job = None
    events: list = []
    brain_session_id = None
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
            "is_running": str(job.get("status") or "")
            in {
                "QUEUED",
                "CLAIMED",
                "PREPROCESSING",
                "INFERENCING",
                "POSTPROCESSING",
                "FAILED_RETRYABLE",
            },
        }

    return {
        "archive_root": str(ar),
        "active": active_session_from_archive(ar),
        "session": {
            **session_summary,
            "brain_session_id": brain_session_id,
            "activity": activity,
        },
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


def cmd_set_active(previews_dir: str) -> dict:
    from utils.runtime_session import write_latest_session_pointer
    from utils.studio_sessions import active_session_from_archive

    previews = Path(previews_dir).expanduser().resolve()
    if not previews.is_dir():
        raise SystemExit(json.dumps({"error": f"not a directory: {previews_dir}"}))
    written = write_latest_session_pointer(previews)
    if written is None:
        raise SystemExit(json.dumps({"error": "failed to write latest_session.json"}))
    try:
        from api import gallery_routes

        gallery_routes._gallery_active_dir_cache = None  # noqa: SLF001
    except Exception:
        pass
    ar = previews.parent.parent
    return {
        "ok": True,
        "latest_session_path": str(written),
        "active": active_session_from_archive(ar),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Studio CLI (JSON stdout)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")

    sub.add_parser("landing-brain")
    sub.add_parser("landing-infra")
    sub.add_parser("infra-overview")

    p_landing_gallery = sub.add_parser("landing-gallery")
    p_landing_gallery.add_argument(
        "--export-dir",
        default=os.environ.get(
            "LUMA_LANDING_GALLERY_EXPORT_DIR",
            "/path/to/Livehouse_Archive/session/exported_images",
        ),
    )
    p_landing_gallery.add_argument("--count", type=int, default=10)

    p_sessions = sub.add_parser("sessions")
    p_sessions.add_argument("--limit", type=int, default=500)

    p_status = sub.add_parser("status")
    p_status.add_argument("previews_dir")

    p_featured = sub.add_parser("featured-frames")
    p_featured.add_argument("previews_dir")

    p_active = sub.add_parser("set-active")
    p_active.add_argument("previews_dir")

    sub.add_parser("ingest-config-get")

    p_icfg = sub.add_parser("ingest-config-put")
    p_icfg.add_argument("json_body")

    args = parser.parse_args()
    if args.cmd == "stats":
        payload = cmd_stats()
    elif args.cmd == "landing-brain":
        payload = cmd_landing_brain()
    elif args.cmd == "landing-infra":
        payload = cmd_landing_infra()
    elif args.cmd == "infra-overview":
        payload = cmd_infra_overview()
    elif args.cmd == "landing-gallery":
        payload = cmd_landing_gallery(args.export_dir, args.count)
    elif args.cmd == "sessions":
        payload = cmd_sessions(args.limit)
    elif args.cmd == "status":
        payload = cmd_status(args.previews_dir)
    elif args.cmd == "featured-frames":
        payload = cmd_featured_frames(args.previews_dir)
    elif args.cmd == "set-active":
        payload = cmd_set_active(args.previews_dir)
    elif args.cmd == "ingest-config-get":
        from utils.studio_ingest_config import read_ingest_config

        payload = read_ingest_config()
    elif args.cmd == "ingest-config-put":
        from utils.studio_ingest_config import save_ingest_config

        body = json.loads(args.json_body)
        payload = save_ingest_config(
            ingest_monitor_path=body.get("ingest_monitor_path"),
            archive_root=body.get("archive_root"),
            session_folder_name=body.get("session_folder_name"),
        )
    else:
        raise SystemExit(f"unknown cmd: {args.cmd}")

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
