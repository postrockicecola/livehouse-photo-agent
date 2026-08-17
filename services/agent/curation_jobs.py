"""Async gallery curation jobs: submit / poll / cancel + deterministic worker.

Chat skills only enqueue and observe. Search+select run in a worker so the
decide→act loop stays short. State lives in ``agent_store.db``, not the
pipeline ``jobs`` SSOT.

Statuses: ``queued → running → done|failed|cancelled``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

from services.agent import store

logger = logging.getLogger(__name__)

STATUSES = ("queued", "running", "done", "failed", "cancelled")
ACTIVE_STATUSES = ("queued", "running")
DEFAULT_TIMEOUT_SEC = 180.0
DEFAULT_LIMIT = 30
JOB_ID_PREFIX = "cur_"
BACKEND_ENV = "LIVEHOUSE_CURATION_JOB_BACKEND"

AbortFn = Callable[[], bool]


def goal_fingerprint(base_dir: str, goal_args: dict[str, Any]) -> str:
    payload = {
        "base_dir": os.path.abspath(str(base_dir or "")),
        "query": str(goal_args.get("query") or ""),
        "limit": int(goal_args.get("limit") or 0),
        "recipe": str(goal_args.get("recipe") or ""),
        "sort_by": str(goal_args.get("sort_by") or ""),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def compile_job_goal(
    user_text: str,
    *,
    limit: Optional[int] = None,
    query: Optional[str] = None,
) -> dict[str, Any]:
    """Build gallery_search args for a delivery-night job."""
    from services.agent import gallery_search_defaults as defaults
    from services.agent.intent_router import semantic_residue
    from services.agent.selection_planner import compile_selection_goal, plan_selection_goal

    text = (user_text or "").strip()
    count = int(limit) if limit else DEFAULT_LIMIT
    count = max(1, min(100, count))
    goal = plan_selection_goal(text, default_count=count)
    if goal is not None:
        args = compile_selection_goal(goal)
    else:
        args = defaults.deliverable_search_args(limit=count)
        residue = (query or "").strip() or semantic_residue(text)
        if residue:
            args["query"] = residue
    args["limit"] = count
    if query and not args.get("query"):
        args["query"] = str(query).strip()
    return args


def _row_to_public(row: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    raw_result = row["result_json"]
    if raw_result:
        try:
            parsed = json.loads(raw_result)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            result = {}
    files = [str(f) for f in (result.get("files") or []) if str(f).strip()]
    out = {
        "job_id": str(row["job_id"]),
        "status": str(row["status"]),
        "owner": str(row["owner"]),
        "session_id": str(row["session_id"]),
        "fingerprint": str(row["fingerprint"]),
        "user_text": str(row["user_text"] or ""),
        "error": str(row["error"] or "") or None,
        "cancel_requested": bool(row["cancel_requested"]),
        "created_at": float(row["created_at"]),
        "updated_at": float(row["updated_at"]),
        "files": files,
        "count": int(result.get("count") or len(files)),
    }
    if result:
        out["result"] = result
    return out


def _get_row(conn: Any, job_id: str) -> Any:
    return conn.execute(
        "SELECT * FROM curation_jobs WHERE job_id=?",
        (job_id,),
    ).fetchone()


def _timed_out(row: Any, *, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    timeout = float(row["timeout_sec"] or DEFAULT_TIMEOUT_SEC)
    started = row["started_at"]
    created = float(row["created_at"])
    anchor = float(started) if started is not None else created
    return (now - anchor) > timeout


def submit_curation_job(
    *,
    owner: str,
    session_id: str,
    base_dir: str,
    goal_args: dict[str, Any],
    user_text: str = "",
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Enqueue a job. Dedupes against queued/running jobs with the same fingerprint."""
    owner = str(owner or "").strip() or "anon:default"
    session_id = str(session_id or "").strip() or "default"
    base_dir = os.path.abspath(str(base_dir or "").strip())
    if not base_dir:
        return {"ok": False, "error": "base_dir is required"}
    fingerprint = goal_fingerprint(base_dir, goal_args)
    timeout_sec = max(5.0, float(timeout_sec or DEFAULT_TIMEOUT_SEC))
    now = time.time()
    conn = store.store_connect()
    try:
        existing = conn.execute(
            """
            SELECT * FROM curation_jobs
            WHERE owner=? AND base_dir=? AND fingerprint=? AND status IN ('queued', 'running')
            ORDER BY id DESC LIMIT 1
            """,
            (owner, base_dir, fingerprint),
        ).fetchone()
        if existing is not None:
            public = _row_to_public(existing)
            public["ok"] = True
            public["deduped"] = True
            return public

        job_id = JOB_ID_PREFIX + uuid.uuid4().hex[:12]
        conn.execute(
            """
            INSERT INTO curation_jobs(
                job_id, owner, session_id, base_dir, fingerprint, status,
                user_text, goal_json, created_at, updated_at, timeout_sec
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                job_id,
                owner,
                session_id,
                base_dir,
                fingerprint,
                "queued",
                user_text,
                json.dumps(goal_args, ensure_ascii=False),
                now,
                now,
                timeout_sec,
            ),
        )
        conn.commit()
        row = _get_row(conn, job_id)
        public = _row_to_public(row)
        public["ok"] = True
        public["deduped"] = False
        return public
    finally:
        conn.close()


def get_curation_job(job_id: str, *, owner: Optional[str] = None) -> Optional[dict[str, Any]]:
    job_id = str(job_id or "").strip()
    if not job_id:
        return None
    conn = store.store_connect()
    try:
        row = _get_row(conn, job_id)
        if row is None:
            return None
        if owner and str(row["owner"]) != str(owner):
            return None
        return _row_to_public(row)
    finally:
        conn.close()


def cancel_curation_job(job_id: str, *, owner: Optional[str] = None) -> dict[str, Any]:
    job_id = str(job_id or "").strip()
    if not job_id:
        return {"ok": False, "error": "job_id is required"}
    now = time.time()
    conn = store.store_connect()
    try:
        row = _get_row(conn, job_id)
        if row is None:
            return {"ok": False, "error": f"unknown job: {job_id}"}
        if owner and str(row["owner"]) != str(owner):
            return {"ok": False, "error": f"unknown job: {job_id}"}
        status = str(row["status"])
        if status in ("done", "failed", "cancelled"):
            public = _row_to_public(row)
            public["ok"] = status == "cancelled"
            if status != "cancelled":
                public["error"] = f"job already {status}; not cancelled"
            return public
        conn.execute(
            """
            UPDATE curation_jobs
            SET cancel_requested=1, status='cancelled', updated_at=?, finished_at=?
            WHERE job_id=? AND status IN ('queued', 'running')
            """,
            (now, now, job_id),
        )
        conn.commit()
        public = _row_to_public(_get_row(conn, job_id))
        public["ok"] = public["status"] == "cancelled"
        return public
    finally:
        conn.close()


def execute_curation(
    base_dir: str,
    goal_args: dict[str, Any],
    *,
    should_abort: Optional[AbortFn] = None,
) -> dict[str, Any]:
    """Run search+select. Does not write curation if ``should_abort`` fires first."""
    from services.agent.skills.gallery_search import GallerySearchSkill, GallerySelectSkill

    if should_abort and should_abort():
        return {"aborted": True}
    search = GallerySearchSkill(base_dir).run(dict(goal_args))
    if not search.ok:
        return {"ok": False, "error": search.error or "gallery_search failed"}
    if should_abort and should_abort():
        return {"aborted": True}
    files = [
        str(f)
        for f in (search.metadata or {}).get("files") or []
        if str(f).strip()
    ]
    if not files:
        return {"ok": True, "files": [], "count": 0, "empty": True}
    select = GallerySelectSkill(base_dir).run({"files": files, "replace": True})
    if not select.ok:
        return {"ok": False, "error": select.error or "gallery_select failed"}
    selected = [
        str(f)
        for f in (select.metadata or {}).get("files")
        or (select.metadata or {}).get("selected_keys")
        or files
        if str(f).strip()
    ]
    return {"ok": True, "files": selected, "count": len(selected), "empty": False}


def run_curation_job(job_id: str) -> dict[str, Any]:
    """Claim a queued job and execute search+select. Cooperative cancel/timeout."""
    job_id = str(job_id or "").strip()
    if not job_id:
        return {"ok": False, "error": "job_id is required"}
    conn = store.store_connect()
    try:
        row = _get_row(conn, job_id)
        if row is None:
            return {"ok": False, "error": f"unknown job: {job_id}"}
        status = str(row["status"])
        if status == "cancelled" or int(row["cancel_requested"] or 0):
            if status != "cancelled":
                now = time.time()
                conn.execute(
                    "UPDATE curation_jobs SET status='cancelled', updated_at=?, finished_at=? WHERE job_id=?",
                    (now, now, job_id),
                )
                conn.commit()
            public = _row_to_public(_get_row(conn, job_id))
            public["ok"] = True
            return public
        if status in ("done", "failed"):
            public = _row_to_public(row)
            public["ok"] = status == "done"
            return public
        if _timed_out(row):
            now = time.time()
            conn.execute(
                """
                UPDATE curation_jobs
                SET status='failed', error='timeout', updated_at=?, finished_at=?
                WHERE job_id=?
                """,
                (now, now, job_id),
            )
            conn.commit()
            public = _row_to_public(_get_row(conn, job_id))
            public["ok"] = False
            return public
        if status == "queued":
            now = time.time()
            conn.execute(
                """
                UPDATE curation_jobs
                SET status='running', started_at=?, updated_at=?
                WHERE job_id=? AND status='queued'
                """,
                (now, now, job_id),
            )
            conn.commit()
            row = _get_row(conn, job_id)
            if row is None or str(row["status"]) != "running":
                public = _row_to_public(_get_row(conn, job_id)) if row else {"ok": False}
                public["ok"] = public.get("status") == "cancelled"
                return public

        goal_args = json.loads(row["goal_json"] or "{}")
        if not isinstance(goal_args, dict):
            goal_args = {}
        base_dir = str(row["base_dir"])

        def _abort() -> bool:
            latest = _get_row(conn, job_id)
            if latest is None:
                return True
            if int(latest["cancel_requested"] or 0) or str(latest["status"]) == "cancelled":
                return True
            return _timed_out(latest)

        result = execute_curation(base_dir, goal_args, should_abort=_abort)
        now = time.time()
        latest = _get_row(conn, job_id)
        if latest is not None and (
            int(latest["cancel_requested"] or 0) or str(latest["status"]) == "cancelled"
        ):
            conn.execute(
                "UPDATE curation_jobs SET status='cancelled', updated_at=?, finished_at=? WHERE job_id=?",
                (now, now, job_id),
            )
            conn.commit()
            public = _row_to_public(_get_row(conn, job_id))
            public["ok"] = True
            return public
        if result.get("aborted") or (latest is not None and _timed_out(latest)):
            reason = "cancelled" if result.get("aborted") else "timeout"
            status_out = "cancelled" if reason == "cancelled" else "failed"
            conn.execute(
                """
                UPDATE curation_jobs
                SET status=?, error=?, updated_at=?, finished_at=?
                WHERE job_id=?
                """,
                (status_out, reason, now, now, job_id),
            )
            conn.commit()
            public = _row_to_public(_get_row(conn, job_id))
            public["ok"] = status_out == "cancelled"
            return public
        if not result.get("ok"):
            conn.execute(
                """
                UPDATE curation_jobs
                SET status='failed', error=?, result_json=?, updated_at=?, finished_at=?
                WHERE job_id=?
                """,
                (str(result.get("error") or "curation failed"), json.dumps(result, ensure_ascii=False), now, now, job_id),
            )
            conn.commit()
            public = _row_to_public(_get_row(conn, job_id))
            public["ok"] = False
            return public
        conn.execute(
            """
            UPDATE curation_jobs
            SET status='done', result_json=?, error=NULL, updated_at=?, finished_at=?
            WHERE job_id=?
            """,
            (json.dumps(result, ensure_ascii=False), now, now, job_id),
        )
        conn.commit()
        public = _row_to_public(_get_row(conn, job_id))
        public["ok"] = True
        return public
    finally:
        conn.close()


def _safe_run(job_id: str) -> None:
    try:
        run_curation_job(job_id)
    except Exception:
        logger.exception("curation job %s crashed", job_id)
        try:
            now = time.time()
            conn = store.store_connect()
            try:
                conn.execute(
                    """
                    UPDATE curation_jobs
                    SET status='failed', error='worker crashed', updated_at=?, finished_at=?
                    WHERE job_id=? AND status IN ('queued', 'running')
                    """,
                    (now, now, job_id),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            logger.exception("failed to mark curation job %s as crashed", job_id)


def schedule_curation_job(job_id: str) -> str:
    """Start the worker. ``defer`` leaves the job queued (tests / L0)."""
    mode = (os.environ.get(BACKEND_ENV) or "auto").strip().lower()
    if mode in ("defer", "off"):
        return "defer"
    if mode == "inline":
        run_curation_job(job_id)
        return "inline"
    if mode in ("celery", "auto"):
        try:
            from celery_app import celery_app

            celery_app.send_task("tasks.run_curation_job", args=[job_id])
            return "celery"
        except Exception:
            if mode == "celery":
                logger.exception("celery schedule failed for %s", job_id)
                return "celery_failed"
    thread = threading.Thread(
        target=_safe_run,
        args=(job_id,),
        name=f"curation-{job_id}",
        daemon=True,
    )
    thread.start()
    return "thread"
