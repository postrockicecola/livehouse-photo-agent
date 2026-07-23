"""SQLite luma_brain ledger helpers (shared by gallery_server and Celery).

**Execution SSOT:** ``jobs.status`` + ``job_events`` (claim, stages, retries, terminal outcomes).
Celery task state is not authoritative. Canonical status sets / documented edges live in this module under
``JOB_STATUSES_*``, ``DOCUMENTED_*_EDGES``, and ``job_transition_is_documented_ssot``.

**Photos ledger:** ``photos.status`` carries ingest progress and per-file analysis *outcome* only
(``NEW`` → ``INGESTED`` → ``ANALYZED``). It must not gate executor admission or in-flight work;
``ANALYZING`` on photos is legacy cleanup only. Job seeding may still *discover* candidate sessions
via ``photos.status = 'INGESTED'`` (work not yet marked analyzed), which is outcome bookkeeping —
not a second state machine for running jobs.

Platform scope: ``jobs.namespace`` and ``jobs.project_key`` (default ``default``) partition work for
multi-product *labeling* without auth. Filter via :func:`list_jobs`, Celery dispatch env
(see :func:`dispatch_scope_from_env`), and ``GET /api/infra/metrics`` /
``GET /api/infra/jobs`` query params; see ``docs/PLATFORM_SCOPE.txt`` and ``luma_brain_schema.sql``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _REPO_ROOT / "luma_brain.db"
_SCHEMA_SQL = _REPO_ROOT / "luma_brain_schema.sql"


class ClaimFenceError(ValueError):
    """Terminal write rejected: claim_generation / worker_id no longer owns the job row."""


def brain_db_path() -> Path:
    raw = os.environ.get("LUMA_BRAIN_DB", str(_DEFAULT_DB))
    return Path(raw).expanduser().resolve()


def _migrate_brain_schema(conn: sqlite3.Connection) -> None:
    """Apply additive SQLite migrations (existing DBs may predate new columns)."""
    cur = conn.execute("PRAGMA table_info(jobs)")
    cols = {str(r[1]) for r in cur.fetchall()}
    if "payload_json" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN payload_json TEXT")
    for name, decl in (
        ("root_job_id", "INTEGER"),
        ("parent_job_id", "INTEGER"),
        ("stage_name", "TEXT"),
        ("stage_order", "INTEGER"),
        ("depends_on_job_id", "INTEGER"),
        ("is_stage", "INTEGER DEFAULT 0"),
        ("namespace", "TEXT NOT NULL DEFAULT 'default'"),
        ("project_key", "TEXT NOT NULL DEFAULT 'default'"),
        # Bumped on claim and stuck-requeue so a zombie writer cannot win after requeue.
        ("claim_generation", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
            cols.add(name)
    if "namespace" in cols and "project_key" in cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_namespace_project ON jobs(namespace, project_key)"
        )
    conn.commit()


def _migrate_model_runs_schema(conn: sqlite3.Connection) -> None:
    """Add inference-ledger columns to ``model_runs`` (older DBs)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_runs' LIMIT 1"
    ).fetchone()
    if t is None:
        return
    cur = conn.execute("PRAGMA table_info(model_runs)")
    cols = {str(r[1]) for r in cur.fetchall()}
    for name, decl in (
        ("primary_provider", "TEXT"),
        ("fallback_provider", "TEXT"),
        ("primary_model", "TEXT"),
        ("final_model", "TEXT"),
        ("end_to_end_latency_ms", "INTEGER"),
        ("provider_latency_ms", "INTEGER"),
        ("fallback_used", "INTEGER NOT NULL DEFAULT 0"),
        ("error_type", "TEXT"),
        ("prompt_length", "INTEGER"),
        ("response_length", "INTEGER"),
        ("prompt_tokens", "INTEGER"),
        ("completion_tokens", "INTEGER"),
        ("total_tokens", "INTEGER"),
        ("outcome_attribution", "TEXT"),
    ):
        if name not in cols:
            conn.execute(f"ALTER TABLE model_runs ADD COLUMN {name} {decl}")
            cols.add(name)
    conn.commit()


def _migrate_model_run_attempts_table(conn: sqlite3.Connection) -> None:
    """Ensure attempt-level ledger table exists (older DBs)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_run_attempts' LIMIT 1"
    ).fetchone()
    if t is not None:
        return
    conn.execute(
        """
        CREATE TABLE model_run_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          model_run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
          seq INTEGER NOT NULL,
          role TEXT NOT NULL CHECK (role IN ('primary', 'fallback')),
          provider_id TEXT NOT NULL,
          model_name TEXT,
          latency_ms INTEGER NOT NULL,
          ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
          error_type TEXT,
          error_message TEXT,
          primary_skipped INTEGER NOT NULL DEFAULT 0 CHECK (primary_skipped IN (0, 1)),
          created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
          UNIQUE(model_run_id, seq)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_run_attempts_run_seq ON model_run_attempts(model_run_id, seq)"
    )
    conn.commit()


def _migrate_artifacts_schema(conn: sqlite3.Connection) -> None:
    """Ensure ``artifacts`` registry exists (DBs created before registry addition)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts' LIMIT 1"
    ).fetchone()
    if t is None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              generated_at INTEGER NOT NULL,
              metadata_json TEXT,
              is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
              stage TEXT,
              source TEXT,
              job_event_id INTEGER REFERENCES job_events(id) ON DELETE SET NULL,
              created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
              content_digest TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_job_kind ON artifacts(job_id, kind)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifacts_job_generated ON artifacts(job_id, generated_at ASC)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind)")
        conn.commit()
        return
    cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(artifacts)").fetchall()}
    if "content_digest" not in cols:
        conn.execute("ALTER TABLE artifacts ADD COLUMN content_digest TEXT")
        conn.commit()


def _migrate_infra_runtime_snapshots_table(conn: sqlite3.Connection) -> None:
    """Ensure optional infra runtime snapshot table exists (older DBs)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='infra_runtime_snapshots' LIMIT 1"
    ).fetchone()
    if t is not None:
        return
    conn.execute(
        """
        CREATE TABLE infra_runtime_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          source TEXT NOT NULL,
          component TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
          UNIQUE(source, component)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_infra_runtime_snapshots_component "
        "ON infra_runtime_snapshots(component, updated_at DESC)"
    )
    conn.commit()


def _migrate_photo_embeddings_table(conn: sqlite3.Connection) -> None:
    """Ensure ``photo_embeddings`` vector index exists (DBs created before embedding support)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='photo_embeddings' LIMIT 1"
    ).fetchone()
    if t is not None:
        return
    conn.execute(
        """
        CREATE TABLE photo_embeddings (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
          model_name TEXT NOT NULL,
          vector BLOB NOT NULL,
          dim INTEGER NOT NULL,
          created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
          UNIQUE(photo_id, model_name)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_photo_embeddings_model "
        "ON photo_embeddings(model_name, photo_id)"
    )
    conn.commit()


def _migrate_infra_metric_samples_table(conn: sqlite3.Connection) -> None:
    """Ensure the rolling control-plane time-series table exists (older DBs)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='infra_metric_samples' LIMIT 1"
    ).fetchone()
    if t is not None:
        return
    conn.execute(
        """
        CREATE TABLE infra_metric_samples (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts INTEGER NOT NULL,
          payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_infra_metric_samples_ts ON infra_metric_samples(ts DESC)"
    )
    conn.commit()


def coerce_positive_job_id(value: Any) -> int | None:
    """Return a positive SQLite ``jobs.id`` for ledger writes, or ``None`` if absent/invalid.

    Treats non-positive integers, non-numeric values, and booleans as absent so callers do not
    hit ``FOREIGN KEY`` failures (e.g. legacy ``job_id=0`` placeholders).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        i = int(value)
    except (TypeError, ValueError):
        return None
    if i <= 0:
        return None
    return i


_SCHEMA_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED: set[str] = set()


def _ensure_brain_schema(conn: sqlite3.Connection, abs_db_path: str) -> None:
    """Apply SQL schema + additive migrations once per process per DB file path."""
    with _SCHEMA_INIT_LOCK:
        if abs_db_path in _SCHEMA_INITIALIZED:
            return
        if _SCHEMA_SQL.is_file():
            conn.executescript(_SCHEMA_SQL.read_text(encoding="utf-8"))
            conn.commit()
        _migrate_brain_schema(conn)
        _migrate_model_runs_schema(conn)
        _migrate_model_run_attempts_table(conn)
        _migrate_artifacts_schema(conn)
        _migrate_infra_runtime_snapshots_table(conn)
        _migrate_infra_metric_samples_table(conn)
        _migrate_photo_embeddings_table(conn)
        _SCHEMA_INITIALIZED.add(abs_db_path)


def brain_connect() -> sqlite3.Connection:
    path = brain_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    abs_path = str(path.resolve())
    conn = sqlite3.connect(abs_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    _ensure_brain_schema(conn, abs_path)
    return conn


def collect_brain_dashboard(
    conn: sqlite3.Connection,
    *,
    sessions_limit: int = 25,
    photos_limit: int = 50,
) -> dict[str, Any]:
    """
    Operator snapshot of the SQLite ledger (sessions / photos + table counts).

    Complements ``GET /api/infra/metrics`` (jobs/workers focus). Photos ``status`` is ingest/outcome only.
    """
    sessions_limit = max(1, min(int(sessions_limit), 200))
    photos_limit = max(1, min(int(photos_limit), 500))

    table_counts: dict[str, int] = {}
    for row in conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name ASC
        """
    ):
        name = str(row[0])
        n = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        table_counts[name] = int(n)

    photos_by_status = {
        str(r[0]): int(r[1])
        for r in conn.execute("SELECT status, COUNT(*) FROM photos GROUP BY status ORDER BY status")
    }

    session_rows = conn.execute(
        """
        SELECT
          s.id,
          s.session_key,
          s.device_id,
          s.archive_root,
          s.previews_dir,
          s.photo_count,
          s.started_at,
          s.closed_at,
          COALESCE(SUM(CASE WHEN p.status = 'INGESTED' THEN 1 ELSE 0 END), 0) AS photos_ingested,
          COALESCE(SUM(CASE WHEN p.status = 'ANALYZED' THEN 1 ELSE 0 END), 0) AS photos_analyzed,
          COALESCE(SUM(CASE WHEN p.status = 'ANALYZING' THEN 1 ELSE 0 END), 0) AS photos_analyzing,
          COALESCE(SUM(CASE WHEN p.status = 'NEW' THEN 1 ELSE 0 END), 0) AS photos_new,
          COUNT(p.id) AS photos_linked
        FROM sessions s
        LEFT JOIN photos p ON p.session_id = s.id
        GROUP BY s.id
        ORDER BY s.started_at DESC
        LIMIT ?
        """,
        (sessions_limit,),
    ).fetchall()

    sessions: list[dict[str, Any]] = []
    for r in session_rows:
        sessions.append(
            {
                "id": int(r["id"]),
                "session_key": str(r["session_key"]),
                "device_id": str(r["device_id"] or ""),
                "archive_root": str(r["archive_root"]),
                "previews_dir": str(r["previews_dir"]),
                "photo_count": int(r["photo_count"] or 0),
                "started_at": int(r["started_at"]),
                "closed_at": int(r["closed_at"]) if r["closed_at"] is not None else None,
                "photos_ingested": int(r["photos_ingested"]),
                "photos_analyzed": int(r["photos_analyzed"]),
                "photos_analyzing": int(r["photos_analyzing"]),
                "photos_new": int(r["photos_new"]),
                "photos_linked": int(r["photos_linked"]),
            }
        )

    photo_rows = conn.execute(
        """
        SELECT
          p.id,
          p.file_hash,
          p.file_path,
          p.status,
          p.device_id,
          p.session_id,
          p.created_at,
          p.updated_at,
          s.session_key
        FROM photos p
        LEFT JOIN sessions s ON s.id = p.session_id
        ORDER BY COALESCE(p.updated_at, p.created_at) DESC, p.id DESC
        LIMIT ?
        """,
        (photos_limit,),
    ).fetchall()

    photos: list[dict[str, Any]] = []
    for r in photo_rows:
        photos.append(
            {
                "id": int(r["id"]),
                "file_hash": str(r["file_hash"]),
                "file_path": str(r["file_path"]),
                "status": str(r["status"]),
                "device_id": str(r["device_id"] or ""),
                "session_id": int(r["session_id"]) if r["session_id"] is not None else None,
                "session_key": str(r["session_key"]) if r["session_key"] is not None else None,
                "created_at": int(r["created_at"]),
                "updated_at": int(r["updated_at"]) if r["updated_at"] is not None else None,
            }
        )

    jobs_by_type = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT job_type, COUNT(*) FROM jobs GROUP BY job_type ORDER BY COUNT(*) DESC"
        )
    }

    return {
        "db_path": str(brain_db_path()),
        "table_counts": table_counts,
        "photos_by_status": photos_by_status,
        "jobs_by_type": jobs_by_type,
        "sessions": sessions,
        "photos": photos,
        "limits": {"sessions": sessions_limit, "photos": photos_limit},
    }


def upsert_infra_runtime_snapshot(
    conn: sqlite3.Connection,
    *,
    source: str,
    component: str,
    payload: dict[str, Any],
) -> None:
    """Replace-or-insert a component-local JSON blob for cross-process metrics (best-effort)."""
    now = int(time.time())
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO infra_runtime_snapshots (source, component, payload_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source, component) DO UPDATE SET
              payload_json = excluded.payload_json,
              updated_at = excluded.updated_at
            """,
            (str(source), str(component), blob, now),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_infra_runtime_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Latest persisted runtime metrics keyed by (``source``, ``component``)."""
    rows = conn.execute(
        """
        SELECT source, component, payload_json, updated_at
        FROM infra_runtime_snapshots
        ORDER BY component ASC, source ASC
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        raw = r["payload_json"]
        try:
            payload = json.loads(str(raw)) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        out.append(
            {
                "source": str(r["source"]),
                "component": str(r["component"]),
                "payload": payload,
                "updated_at": int(r["updated_at"]),
            }
        )
    return out


def record_infra_metric_sample(
    conn: sqlite3.Connection,
    *,
    payload: dict[str, Any],
    min_interval_sec: int = 5,
    retain_sec: int = 86400,
) -> bool:
    """
    Append a compact control-plane time-series sample (best-effort, throttled + pruned).

    Throttled so callers can fire it from a read endpoint without table bloat: skips the
    write when the newest row is younger than ``min_interval_sec``. Prunes rows older than
    ``retain_sec``. Returns ``True`` when a row was inserted.
    """
    now = int(time.time())
    blob = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    conn.execute("BEGIN IMMEDIATE")
    try:
        last = conn.execute("SELECT ts FROM infra_metric_samples ORDER BY ts DESC LIMIT 1").fetchone()
        if last is not None and (now - int(last["ts"])) < max(1, int(min_interval_sec)):
            conn.rollback()
            return False
        conn.execute("INSERT INTO infra_metric_samples (ts, payload_json) VALUES (?, ?)", (now, blob))
        if retain_sec > 0:
            conn.execute("DELETE FROM infra_metric_samples WHERE ts < ?", (now - int(retain_sec),))
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def list_infra_metric_samples(
    conn: sqlite3.Connection,
    *,
    since_sec: int | None = None,
    limit: int = 240,
) -> list[dict[str, Any]]:
    """Recent control-plane samples (oldest first) for sparklines / trend seeding."""
    limit = max(1, min(int(limit), 2000))
    args: list[Any] = []
    where = ""
    if since_sec is not None:
        where = "WHERE ts >= ?"
        args.append(int(since_sec))
    rows = conn.execute(
        f"SELECT ts, payload_json FROM infra_metric_samples {where} ORDER BY ts DESC LIMIT ?",
        (*args, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in reversed(rows):
        raw = r["payload_json"]
        try:
            payload = json.loads(str(raw)) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append({"ts": int(r["ts"]), **payload})
    return out


def claim_ingested_for_analysis(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """
    Legacy name / read-only helper: sessions that have at least one photo in ``INGESTED``.

    **Not** an execution claim: workers claim ``jobs`` rows only. Use this for diagnostics or
    archive UX that lists “files landed, analysis outcome not recorded yet”.
    """
    rows = conn.execute(
        "SELECT DISTINCT session_id FROM photos WHERE status = 'INGESTED' AND session_id IS NOT NULL"
    ).fetchall()
    sids = [int(r[0]) for r in rows if r[0] is not None]
    if not sids:
        return []
    qm = ",".join("?" * len(sids))
    cur = conn.execute(
        f"SELECT id, previews_dir FROM sessions WHERE id IN ({qm}) ORDER BY id ASC",
        sids,
    )
    return [(int(r[0]), str(r[1])) for r in cur.fetchall()]


def finalize_analyzed(conn: sqlite3.Connection, session_ids: list[int]) -> None:
    """
    Outcome ledger: mark photos ``ANALYZED`` after a successful analysis job.

    Updates rows still awaiting outcome (``INGESTED``). Includes legacy ``ANALYZING`` rows so old DBs
    converge without relying on photo status for executor control.
    """
    if not session_ids:
        return
    now = int(time.time())
    qm = ",".join("?" * len(session_ids))
    conn.execute(
        f"UPDATE photos SET status = 'ANALYZED', updated_at = ? "
        f"WHERE session_id IN ({qm}) AND status IN ('INGESTED', 'ANALYZING')",
        (now, *session_ids),
    )
    conn.commit()


def release_analyzing_sessions(conn: sqlite3.Connection, session_ids: list[int]) -> None:
    """
    Legacy: rollback ``ANALYZING`` → ``INGESTED`` for stale photo rows.

    Execution retries are driven by ``jobs.status`` (e.g. ``FAILED_RETRYABLE``); this only cleans
    pre-job-centric ``photos`` states if anything still wrote ``ANALYZING``.
    """
    if not session_ids:
        return
    now = int(time.time())
    qm = ",".join("?" * len(session_ids))
    conn.execute(
        f"UPDATE photos SET status = 'INGESTED', updated_at = ? "
        f"WHERE session_id IN ({qm}) AND status = 'ANALYZING'",
        (now, *session_ids),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# AI-infra: jobs / job_events / workers / model_runs.
# Runnable work and lifecycle are owned by ``jobs`` (+ events); photos.status is ingest/outcome only.
# ---------------------------------------------------------------------------
# ``workers.status`` (see schema CHECK): control-plane + runtime. Only ``ONLINE`` may claim new
# work; ``DRAINING`` still runs in-flight work but takes no new claims; ``PAUSED`` / ``ERROR`` stop
# new claims. Heartbeat from workers must not clobber operator-set PAUSED/DRAINING/ERROR.
WORKER_STATUS_CONTROL_BLOCK_HEARTBEAT = frozenset({"PAUSED", "DRAINING", "ERROR"})

# Platform scope: one deployment may host many logical projects (SSOT: ``jobs`` columns; no per-worker scope in v1).
_DEFAULT_JOB_NAMESPACE = "default"
_DEFAULT_PROJECT_KEY = "default"


def _jobs_scope_sql_fragment(
    namespace: str | None,
    project_key: str | None,
    *,
    table_alias: str | None = None,
) -> tuple[str, list[Any]]:
    """Return a leading ``AND ...`` fragment and bound args for ``jobs`` scope filters."""
    pref = f"{table_alias}." if table_alias else ""
    parts: list[str] = []
    args: list[Any] = []
    if namespace is not None:
        parts.append(f"{pref}namespace = ?")
        args.append(_coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE))
    if project_key is not None:
        parts.append(f"{pref}project_key = ?")
        args.append(_coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY))
    if not parts:
        return "", []
    return " AND " + " AND ".join(parts), args


def dispatch_scope_from_env() -> tuple[str | None, str | None]:
    """
    Optional Celery dispatch filter from env (single deployment, multiple logical projects).

    - ``LIVEHOUSE_DISPATCH_NAMESPACE`` — when set (non-empty after trim), restrict runnable listing
      and provider dispatch signals to this namespace.
    - ``LIVEHOUSE_DISPATCH_PROJECT_KEY`` — same for project key (can combine with namespace).

    Empty or unset env → ``(None, None)`` (no filter; legacy behavior).
    """
    raw_ns = os.environ.get("LIVEHOUSE_DISPATCH_NAMESPACE")
    raw_pk = os.environ.get("LIVEHOUSE_DISPATCH_PROJECT_KEY")

    def _non_empty(raw: str | None) -> str | None:
        if raw is None:
            return None
        t = str(raw).strip()
        return t if t else None

    ns = _non_empty(raw_ns)
    pk = _non_empty(raw_pk)
    if ns is not None:
        ns = _coalesce_job_scope(ns, default=_DEFAULT_JOB_NAMESPACE)
    if pk is not None:
        pk = _coalesce_job_scope(pk, default=_DEFAULT_PROJECT_KEY)
    return ns, pk


def _coalesce_job_scope(value: str | None, *, default: str) -> str:
    t = (value if value is not None else default).strip()
    return t if t else default


_RUNNABLE_JOB_STATUSES = ("QUEUED", "FAILED_RETRYABLE")
_ACTIVE_JOB_STATUSES = ("CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING")

# ---------------------------------------------------------------------------
# SSOT ``jobs.status`` lifecycle (see also ``services.job_lifecycle`` orchestration helpers).
#
# Attempt / retry semantics (single consistent meaning across claim + fail_retryable):
#   - ``attempt`` increments **exactly once per successful claim** (QUEUED or FAILED_RETRYABLE → CLAIMED).
#   - ``max_attempts`` is the **maximum number of claims / executions**. After failing following the Nth claim,
#     if ``attempt >= max_attempts``, :func:`fail_job_retryable` promotes to DEAD_LETTERED instead of FAILED_RETRYABLE.
#   - Stuck requeue (:func:`requeue_stuck_jobs`) moves ACTIVE_PIPELINE → QUEUED **without**
#     incrementing ``attempt`` — it frees a phantom claim, not a failed execution retry.
#
# FAILED_RETRYABLE remains “open” until manual retry / success / cancel / exhaustion → DL.
# FAILED_PERMANENT is immediate terminal (see ``services.job_errors.classify_exception`` for routing hints).
# ---------------------------------------------------------------------------

JOB_STATUSES_RUNNABLE = frozenset({"QUEUED", "FAILED_RETRYABLE"})
JOB_STATUSES_ACTIVE_PIPELINE = frozenset({"CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"})
JOB_STATUSES_TERMINAL = frozenset({"SUCCEEDED", "FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"})
# FAILED_RETRYABLE is terminal for *the current enqueue/claim burst* but may return to runnable via reclaim.
JOB_STATUSES_FAILED_OPEN = frozenset({"FAILED_RETRYABLE"})
JOB_STATUSES_MANUAL_RETRY_ALLOWED_FROM = frozenset(
    {
        "DEAD_LETTERED",
        "FAILED_PERMANENT",
        "FAILED_RETRYABLE",
        "SUCCEEDED",
        "CANCELLED",
    }
)

DOCUMENTED_PIPELINE_FORWARD_EDGES = frozenset(
    {
        ("CLAIMED", "PREPROCESSING"),
        ("PREPROCESSING", "INFERENCING"),
        ("INFERENCING", "POSTPROCESSING"),
        ("POSTPROCESSING", "SUCCEEDED"),
        # ANALYZE_* path skips POSTPROCESSING and succeeds straight from inferencing today.
        ("INFERENCING", "SUCCEEDED"),
    }
)
DOCUMENTED_MAINTENANCE_EDGES = frozenset(
    {
        ("QUEUED", "CLAIMED"),
        ("FAILED_RETRYABLE", "CLAIMED"),
        ("FAILED_RETRYABLE", "DEAD_LETTERED"),  # reconcile_exhausted_retryable_to_dead_letter + exhaustion in fail_retryable
        # Stuck recovery: any active slot back to the dispatch queue.
        ("CLAIMED", "QUEUED"),
        ("PREPROCESSING", "QUEUED"),
        ("INFERENCING", "QUEUED"),
        ("POSTPROCESSING", "QUEUED"),
        # Operator actions.
        ("QUEUED", "CANCELLED"),
        ("CLAIMED", "CANCELLED"),
        ("PREPROCESSING", "CANCELLED"),
        ("INFERENCING", "CANCELLED"),
        ("POSTPROCESSING", "CANCELLED"),
        ("FAILED_RETRYABLE", "CANCELLED"),
        # manual_retry_job
        ("DEAD_LETTERED", "QUEUED"),
        ("FAILED_PERMANENT", "QUEUED"),
        ("FAILED_RETRYABLE", "QUEUED"),
        ("SUCCEEDED", "QUEUED"),
        ("CANCELLED", "QUEUED"),
    }
)


def job_status_is_terminal(status: str | None) -> bool:
    """True for rows that no longer execute unless an operator uses ``manual_retry_job`` (where allowed)."""
    s = str(status or "").strip()
    return s in JOB_STATUSES_TERMINAL


def job_transition_is_documented_ssot(from_status: str | None, to_status: str | None) -> bool:
    """
    Whether ``(from_status, to_status)`` matches a **documented** transition produced by this module’s helpers.

    Not exhaustive of every historical row in ``job_events`` (``update_job_status`` does not hard-enforce guards),
    but covers claim / pipeline forwards / cancellations / stuck requeue / retry / exhaustion paths.

    Operators may use manual SQL or drift older DBs — treat False as “review”, not necessarily “illegal”.
    """
    fs = str(from_status or "").strip()
    ts = str(to_status or "").strip()
    if not fs or not ts:
        return False
    pair = (fs, ts)
    if pair in DOCUMENTED_PIPELINE_FORWARD_EDGES or pair in DOCUMENTED_MAINTENANCE_EDGES:
        return True
    # Fail / succeed shortcuts from arbitrary pipeline depth (executor records failure from current stage).
    if fs in JOB_STATUSES_ACTIVE_PIPELINE and ts in (
        "SUCCEEDED",
        "FAILED_RETRYABLE",
        "FAILED_PERMANENT",
        "DEAD_LETTERED",
    ):
        return True
    return False


# ``PIPELINE_STAGE`` jobs may be QUEUED but unclaimable until ``depends_on_job_id`` is SUCCEEDED.
def _sql_dependency_satisfied() -> str:
    return (
        "(jobs.depends_on_job_id IS NULL OR "
        "(SELECT d.status FROM jobs d WHERE d.id = jobs.depends_on_job_id) = 'SUCCEEDED')"
    )


def _status_placeholders(values: tuple[str, ...]) -> str:
    return ",".join("?" for _ in values)


def count_active_jobs_for_worker(conn: sqlite3.Connection, worker_id: int) -> int:
    """Pipeline execution slots currently owned by ``worker_id`` (see ``_ACTIVE_JOB_STATUSES``)."""
    qm = _status_placeholders(_ACTIVE_JOB_STATUSES)
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM jobs WHERE worker_id = ? AND status IN ({qm})",
        (int(worker_id), *_ACTIVE_JOB_STATUSES),
    ).fetchone()
    return int(row["c"]) if row is not None else 0


def append_job_event(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    from_status: str | None = None,
    to_status: str | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append one job lifecycle event and return event id."""
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    cur = conn.execute(
        """
        INSERT INTO job_events (job_id, from_status, to_status, created_at, message, payload_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (job_id, from_status, to_status, int(time.time()), message, payload_json),
    )
    return int(cur.lastrowid)


def create_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    session_id: int | None = None,
    photo_id: int | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    provider: str | None = None,
    model_name: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
    root_job_id: int | None = None,
    parent_job_id: int | None = None,
    stage_name: str | None = None,
    stage_order: int | None = None,
    depends_on_job_id: int | None = None,
    is_stage: int = 0,
    namespace: str | None = None,
    project_key: str | None = None,
) -> int:
    """Create one QUEUED job row and emit initial job_event (single source of truth for runnable work)."""
    now = int(time.time())
    ns = _coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE)
    proj = _coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY)
    payload_json = json.dumps(payload, ensure_ascii=False) if payload is not None else None
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            """
            INSERT INTO jobs (
                job_type, session_id, photo_id, status, priority, attempt, max_attempts,
                provider, model_name, fallback_used, enqueued_at, created_at, updated_at, trace_id,
                payload_json,
                root_job_id, parent_job_id, stage_name, stage_order, depends_on_job_id, is_stage,
                namespace, project_key
            ) VALUES (?, ?, ?, 'QUEUED', ?, 0, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_type,
                session_id,
                photo_id,
                priority,
                max_attempts,
                provider,
                model_name,
                now,
                now,
                now,
                trace_id,
                payload_json,
                root_job_id,
                parent_job_id,
                stage_name,
                stage_order,
                depends_on_job_id,
                is_stage,
                ns,
                proj,
            ),
        )
        job_id = int(cur.lastrowid)
        ev_payload: dict[str, Any] = {"priority": priority, "max_attempts": max_attempts}
        if payload is not None:
            ev_payload["payload_keys"] = sorted(payload.keys())
        append_job_event(
            conn,
            job_id=job_id,
            to_status="QUEUED",
            message="job enqueued",
            payload=ev_payload,
        )
        conn.commit()
        return job_id
    except Exception:
        conn.rollback()
        raise


def analyze_path_job_payload(
    *,
    source_dir: str,
    config_path: str = "configs/livehouse.yaml",
    max_workers: int | None = None,
    enable_checkpoint: bool = True,
    force_full_rerun: bool = False,
) -> dict[str, Any]:
    """Canonical ANALYZE_PATH dict stored in ``jobs.payload_json`` (read by ``tasks.run_job``)."""
    if force_full_rerun:
        enable_checkpoint = False
    return {
        "source_dir": source_dir,
        "config_path": config_path,
        "max_workers": max_workers,
        "enable_checkpoint": enable_checkpoint,
        "force_full_rerun": bool(force_full_rerun),
    }


def curate_path_job_payload(
    *,
    source_dir: str,
    config_path: str = "configs/livehouse.yaml",
    agent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical CURATE_PATH dict stored in ``jobs.payload_json`` (read by ``tasks.run_job``).

    ``agent`` carries optional :class:`services.agent.types.AgentConfig` overrides
    (target_keepers, max_inferences, allow_escalation, ...).
    """
    payload: dict[str, Any] = {"source_dir": source_dir, "config_path": config_path}
    if agent:
        payload["agent"] = dict(agent)
    return payload


def create_curate_path_job(
    conn: sqlite3.Connection,
    *,
    source_dir: str,
    config_path: str = "configs/livehouse.yaml",
    agent: dict[str, Any] | None = None,
    trace_id: str | None = None,
    namespace: str | None = None,
    project_key: str | None = None,
) -> int:
    """Insert a QUEUED ``CURATE_PATH`` job; dispatch ``tasks.run_job`` after this returns."""
    payload = curate_path_job_payload(source_dir=source_dir, config_path=config_path, agent=agent)
    return create_job(
        conn,
        job_type="CURATE_PATH",
        session_id=None,
        trace_id=trace_id,
        payload=payload,
        namespace=namespace,
        project_key=project_key,
    )


def create_analyze_path_job(
    conn: sqlite3.Connection,
    *,
    source_dir: str,
    config_path: str = "configs/livehouse.yaml",
    max_workers: int | None = None,
    enable_checkpoint: bool = True,
    force_full_rerun: bool = False,
    trace_id: str | None = None,
    namespace: str | None = None,
    project_key: str | None = None,
) -> int:
    """Insert QUEUED ``ANALYZE_PATH`` job; API layer should call this synchronously before dispatching Celery."""
    payload = analyze_path_job_payload(
        source_dir=source_dir,
        config_path=config_path,
        max_workers=max_workers,
        enable_checkpoint=enable_checkpoint,
        force_full_rerun=force_full_rerun,
    )
    return create_job(
        conn,
        job_type="ANALYZE_PATH",
        session_id=None,
        trace_id=trace_id,
        payload=payload,
        namespace=namespace,
        project_key=project_key,
    )


def patch_job_payload(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    patch: dict[str, Any],
) -> None:
    """Shallow-merge patch into jobs.payload_json (executor hints); does not emit job_events (batch-safe)."""
    if not patch:
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT payload_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"job id not found: {job_id}")
        base: dict[str, Any] = {}
        raw = row["payload_json"]
        if raw:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                base = loaded
        base.update(patch)
        conn.execute(
            "UPDATE jobs SET payload_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(base, ensure_ascii=False), int(time.time()), job_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def seed_analyze_session_jobs(
    conn: sqlite3.Connection,
    *,
    job_type: str = "ANALYZE_SESSION",
    limit: int = 200,
    trace_id: str | None = None,
) -> dict[str, Any]:
    """
    Ensure one runnable job per session that still has photos pending analysis *outcome*.

    Candidate discovery uses ``photos.status = 'INGESTED'`` (ingest complete, not yet ``ANALYZED``).
    That is ledger/outcome semantics — not executor state. Duplicate runnable jobs are skipped by
    querying ``jobs`` for an active/retryable row per session.

    Returns the same shape as tasks.create_analysis_jobs for API compatibility.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT p.session_id
        FROM photos p
        WHERE p.status = 'INGESTED'
          AND p.session_id IS NOT NULL
        ORDER BY p.session_id ASC
        LIMIT ?
        """,
        (max(1, limit),),
    ).fetchall()
    session_ids = [int(r["session_id"]) for r in rows]
    created_ids: list[int] = []
    skipped_sessions: list[int] = []
    for sid in session_ids:
        existing = conn.execute(
            """
            SELECT id
            FROM jobs
            WHERE job_type = ?
              AND session_id = ?
              AND status IN (
                'QUEUED','CLAIMED','PREPROCESSING','INFERENCING','POSTPROCESSING','FAILED_RETRYABLE'
              )
            LIMIT 1
            """,
            (job_type, sid),
        ).fetchone()
        if existing is not None:
            skipped_sessions.append(sid)
            continue
        job_id = create_job(conn, job_type=job_type, session_id=sid, trace_id=trace_id)
        created_ids.append(job_id)
    return {
        "ok": True,
        "job_type": job_type,
        "candidate_sessions": len(session_ids),
        "created_jobs": len(created_ids),
        "created_job_ids": created_ids,
        "skipped_sessions": skipped_sessions,
    }


def list_runnable_analyze_jobs_for_ingested_sessions(
    conn: sqlite3.Connection,
    *,
    job_type: str = "ANALYZE_SESSION",
    limit: int = 200,
    namespace: str | None = None,
    project_key: str | None = None,
) -> list[int]:
    """
    Runnable ``ANALYZE_SESSION`` job ids for Celery dispatch (after :func:`seed_analyze_session_jobs`).

    **Dispatch SSOT:** filters ``jobs`` only (status + dependency gate). Does **not** re-check
    ``photos.status`` — executor eligibility is purely the job row. Historical name retained for imports.

    **Stale jobs:** a ``QUEUED`` row may still run if ``photos`` were manually marked ``ANALYZED``;
    operators should cancel or complete such jobs via infra APIs if needed.
    """
    runnable_qm = _status_placeholders(_RUNNABLE_JOB_STATUSES)
    scope_frag, scope_args = _jobs_scope_sql_fragment(namespace, project_key, table_alias="j")
    rows = conn.execute(
        f"""
        SELECT DISTINCT j.id
        FROM jobs j
        WHERE j.job_type = ?
          AND j.status IN ({runnable_qm})
          AND j.session_id IS NOT NULL
          AND (j.depends_on_job_id IS NULL OR (
            SELECT d.status FROM jobs d WHERE d.id = j.depends_on_job_id
          ) = 'SUCCEEDED')
          {scope_frag}
        ORDER BY j.enqueued_at ASC, j.id ASC
        LIMIT ?
        """,
        (job_type, *_RUNNABLE_JOB_STATUSES, *scope_args, max(1, limit)),
    ).fetchall()
    return [int(r[0]) for r in rows]


def list_runnable_job_ids_for_dispatch(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
    namespace: str | None = None,
    project_key: str | None = None,
) -> list[int]:
    """
    Runnable ``QUEUED`` / ``FAILED_RETRYABLE`` job ids (dependency satisfied, attempts left).

    Used by periodic Celery dispatch so work re-queued after worker loss (or never dispatched)
    still reaches ``tasks.run_job`` without relying solely on ingest seeding.

    Optional ``namespace`` / ``project_key`` narrow the candidate set (same semantics as
    :func:`dispatch_scope_from_env`).
    """
    cap = max(1, min(5000, int(limit)))
    runnable_qm = _status_placeholders(_RUNNABLE_JOB_STATUSES)
    dep = _sql_dependency_satisfied()
    scope_frag, scope_args = _jobs_scope_sql_fragment(namespace, project_key, table_alias="jobs")
    rows = conn.execute(
        f"""
        SELECT id FROM jobs
        WHERE status IN ({runnable_qm})
          AND attempt < max_attempts
          AND {dep}
          {scope_frag}
        ORDER BY priority DESC, enqueued_at ASC, id ASC
        LIMIT ?
        """,
        (*_RUNNABLE_JOB_STATUSES, *scope_args, cap),
    ).fetchall()
    return [int(r[0]) for r in rows]


def get_job(conn: sqlite3.Connection, *, job_id: int) -> dict[str, Any] | None:
    """Load one job by id."""
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def list_job_ids_by_trace_id(conn: sqlite3.Connection, *, trace_id: str) -> list[int]:
    """Return job ids with this ``trace_id`` (oldest / lowest id first)."""
    tid = (trace_id or "").strip()
    if not tid:
        return []
    rows = conn.execute(
        "SELECT id FROM jobs WHERE trace_id = ? ORDER BY id ASC",
        (tid,),
    ).fetchall()
    return [int(r[0]) for r in rows]


def executor_pool_headroom_for_dispatch(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Remaining slots per ``required_executor_class`` for ONLINE workers.

    wildcard/Omnivore pools (``general``, legacy ``celery`` / ``generic``) contribute to **every**
    routing bucket so single-worker Celery setups stay compatible.

    ``inflight`` is derived from **live** ``jobs`` rows (``COUNT`` in active pipeline statuses),
    not the ``workers.inflight`` mirror column — so Celery concurrency > 1 matches admission.
    """
    from services.worker_pools import JOB_ROUTING_EXECUTOR_CLASSES, split_legacy_and_specific_capacity

    qm = _status_placeholders(_ACTIVE_JOB_STATUSES)
    rows = conn.execute(
        f"""
        SELECT w.id AS worker_id, w.worker_type, w.capacity,
               (
                 SELECT COUNT(*) FROM jobs j
                 WHERE j.worker_id = w.id AND j.status IN ({qm})
               ) AS live_inflight
        FROM workers w
        WHERE w.status = 'ONLINE'
        """,
        _ACTIVE_JOB_STATUSES,
    ).fetchall()
    wildcard = 0
    specific = {p: 0 for p in JOB_ROUTING_EXECUTOR_CLASSES}
    for r in rows:
        free, tag = split_legacy_and_specific_capacity(
            worker_type_raw=r["worker_type"],
            capacity=r["capacity"],
            inflight=r["live_inflight"],
        )
        if tag is None:
            wildcard += free
        elif tag in specific:
            specific[tag] += free
        else:
            from services.worker_pools import EXECUTOR_INFERENCE

            specific[EXECUTOR_INFERENCE] += free
    effective = {p: wildcard + specific[p] for p in JOB_ROUTING_EXECUTOR_CLASSES}
    return {
        "wildcard_headroom": wildcard,
        "specific_only_headroom": specific,
        "effective_headroom_by_required_pool": effective,
    }


def cluster_headroom_for_dispatch(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """
    Sum ``capacity - live_inflight`` for ``ONLINE`` workers (AI-infra style dispatch signal).

    Used by the scheduler to avoid blasting more Celery ``run_job`` tasks than the
    pool can plausibly execute; jobs remain ``QUEUED`` in SSOT when not dispatched.

    Live inflight counts **pipeline-active** rows on ``jobs`` for each ONLINE worker —
    aligns with admission even when Celery concurrency > 1. The ``workers.inflight``
    column is a best-effort mirror for dashboards only.

    ``total_worker_rows`` counts all ``workers`` rows. When this is >0 but
    ``total_capacity`` is 0 (e.g. every worker is ``PAUSED``/``DRAINING``), schedulers
    should treat **admission** as fully saturated (``headroom`` 0) so dispatch stops.

    **Control-plane states:** only ``status = ONLINE`` contributes ``capacity`` here.
    ``DRAINING`` / ``PAUSED`` / ``ERROR`` / ``OFFLINE`` rows are omitted from totals (no new Celery work),
    consistent with ``worker_runtime_admission``.

    ``executor_pools`` breaks headroom down by logical executor class so dispatch can skip jobs whose
    pool has no ONLINE capacity (see ``services.worker_pools``).
    """
    qm = _status_placeholders(_ACTIVE_JOB_STATUSES)
    row = conn.execute(
        f"""
        SELECT
          (SELECT COUNT(*) FROM workers) AS total_worker_rows,
          (SELECT COALESCE(SUM(CASE WHEN status = 'ONLINE' THEN 1 ELSE 0 END), 0) FROM workers) AS online_workers,
          (SELECT COALESCE(SUM(CASE WHEN status = 'ONLINE' THEN capacity ELSE 0 END), 0) FROM workers) AS total_capacity,
          (
            SELECT COALESCE(SUM(live), 0) FROM (
              SELECT (
                SELECT COUNT(*) FROM jobs j
                WHERE j.worker_id = w.id AND j.status IN ({qm})
              ) AS live
              FROM workers w
              WHERE w.status = 'ONLINE'
            )
          ) AS total_inflight
        """,
        _ACTIVE_JOB_STATUSES,
    ).fetchone()
    if row is None:
        return {
            "total_worker_rows": 0,
            "online_workers": 0,
            "total_capacity": 0,
            "total_inflight": 0,
            "headroom": 0,
            "executor_pools": executor_pool_headroom_for_dispatch(conn),
        }
    cap = int(row["total_capacity"] or 0)
    inf = int(row["total_inflight"] or 0)
    headroom = max(0, cap - inf)
    nrows = int(row["total_worker_rows"] or 0)
    return {
        "total_worker_rows": nrows,
        "online_workers": int(row["online_workers"] or 0),
        "total_capacity": cap,
        "total_inflight": inf,
        "headroom": headroom,
        "executor_pools": executor_pool_headroom_for_dispatch(conn),
    }


def worker_runtime_admission(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
) -> dict[str, Any]:
    """
    Whether this worker may **claim a new** job: ``ONLINE`` and live inflight < capacity.

    Live inflight = count of ``jobs`` rows in ``CLAIMED|PREPROCESSING|INFERENCING|POSTPROCESSING``
    with ``worker_id`` set — matches Celery prefork running multiple ``run_job`` tasks on one SSOT row.

    **Status semantics (scheduling):**
        - ``ONLINE`` — eligible for claims and counts toward dispatch headroom (subject to capacity).
        - ``DRAINING`` — finish in-flight work only; denies new claims; excluded from ONLINE headroom
          so orchestration does not assign fresh ``run_job`` via cluster capacity.
        - ``PAUSED`` — operator halt; denies new claims; excluded from ONLINE headroom; heartbeats from
          the worker binary do not downgrade this state (:func:`heartbeat_worker`).
        - ``ERROR`` — broken / quarantined; denies new claims; excluded from ONLINE headroom; typically
          set by failed tasks or operators until ``resume``.
        - ``OFFLINE`` — not accepting work; denies new claims; excluded from ONLINE headroom.

    ``workers.inflight`` is updated opportunistically for telemetry; admission never trusts it alone.
    """
    row = conn.execute(
        "SELECT id, status, capacity FROM workers WHERE id = ?",
        (int(worker_id),),
    ).fetchone()
    live = count_active_jobs_for_worker(conn, int(worker_id))
    if row is None:
        return {
            "ok": False,
            "reason": "worker_not_found",
            "message": "worker id not in workers table",
        }
    st = str(row["status"] or "")
    cap = int(row["capacity"] or 0)
    inf = live
    if st != "ONLINE":
        return {
            "ok": False,
            "reason": "worker_status",
            "message": f"status {st!r} does not accept new work (need ONLINE)",
            "status": st,
            "capacity": cap,
            "inflight": inf,
        }
    if cap <= 0:
        return {
            "ok": False,
            "reason": "zero_capacity",
            "message": "capacity must be > 0 to claim",
            "status": st,
            "capacity": cap,
            "inflight": inf,
        }
    if inf >= cap:
        return {
            "ok": False,
            "reason": "at_capacity",
            "message": "live_inflight >= capacity",
            "status": st,
            "capacity": cap,
            "inflight": inf,
        }
    return {
        "ok": True,
        "reason": None,
        "message": None,
        "status": st,
        "capacity": cap,
        "inflight": inf,
    }


def worker_executor_claim_gate_for_job(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
    job_row: dict[str, Any],
) -> dict[str, Any]:
    """
    Whether the worker row's executor pool may claim ``job_row`` (after runtime admission).

    See ``services.worker_pools`` for routing rules and legacy omnivore pools.
    """
    from services.worker_pools import required_executor_class_for_job, worker_pool_accepts_job

    required = required_executor_class_for_job(job_row)
    wrow = conn.execute(
        "SELECT worker_type FROM workers WHERE id = ?",
        (int(worker_id),),
    ).fetchone()
    if wrow is None:
        return {
            "ok": False,
            "reason": "worker_not_found",
            "required_executor_class": required,
            "worker_executor_pool": None,
        }
    wt = str(wrow["worker_type"] or "")
    if not worker_pool_accepts_job(wt, required):
        return {
            "ok": False,
            "reason": "executor_pool_mismatch",
            "required_executor_class": required,
            "worker_executor_pool": wt,
        }
    return {
        "ok": True,
        "reason": None,
        "required_executor_class": required,
        "worker_executor_pool": wt,
    }


def set_worker_control_status(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
    to_status: str,
) -> dict[str, Any]:
    """
    Operator/runtime control: set ``workers.status`` (``ONLINE|OFFLINE|PAUSED|DRAINING|ERROR``).

    Used by the infra API (pause / resume / drain / error). Emits no separate event table; SSOT
    is the row + ``last_heartbeat`` for ops tooling.
    """
    allowed = ("ONLINE", "OFFLINE", "PAUSED", "DRAINING", "ERROR")
    if to_status not in allowed:
        return {
            "ok": False,
            "worker_id": int(worker_id),
            "status": None,
            "message": f"invalid status (allowed: {allowed})",
        }
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("SELECT id, status FROM workers WHERE id = ?", (int(worker_id),)).fetchone()
        if cur is None:
            conn.rollback()
            return {
                "ok": False,
                "worker_id": int(worker_id),
                "status": None,
                "message": "worker not found",
            }
        from_status = str(cur["status"] or "")
        conn.execute(
            """
            UPDATE workers
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (to_status, now, int(worker_id)),
        )
        conn.commit()
        return {
            "ok": True,
            "worker_id": int(worker_id),
            "status": to_status,
            "message": None,
            "from_status": from_status,
        }
    except Exception:
        conn.rollback()
        raise


def get_jobs_dispatch_metadata(
    conn: sqlite3.Connection,
    job_ids: list[int],
) -> list[dict[str, Any]]:
    """
    Minimal columns for dispatch policy: ``id``, ``job_type``, ``priority``, ``enqueued_at``,
    ``provider``, ``stage_name``, ``payload_json`` (for executor routing), ``namespace``,
    ``project_key`` (platform scope), plus ``required_executor_class`` (derived).

    Preserves the order of ``job_ids`` in the result (skips missing ids).
    """
    from services.worker_pools import required_executor_class_for_job

    if not job_ids:
        return []
    qm = ",".join("?" * len(job_ids))
    rows = conn.execute(
        f"""
        SELECT id, job_type, priority, enqueued_at, provider, stage_name, payload_json,
               namespace, project_key
        FROM jobs
        WHERE id IN ({qm})
        """,
        job_ids,
    ).fetchall()
    by_id = {int(r["id"]): dict(r) for r in rows}
    out: list[dict[str, Any]] = []
    for jid in job_ids:
        r = by_id.get(int(jid))
        if r is not None:
            d = dict(r)
            d["required_executor_class"] = required_executor_class_for_job(d)
            out.append(d)
    return out


def gather_provider_dispatch_signals(
    conn: sqlite3.Connection,
    *,
    finished_window_seconds: int = 3600,
    namespace: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    """
    SQLite-side signals for provider-aware dispatch (complements in-process ``infra.metrics``).

    - ``inflight_by_provider``: jobs in active pipeline statuses, grouped by normalized provider key
    - ``finished_stats_by_provider``: jobs with ``finished_at`` in the window (throughput proxy)

    Optional ``namespace`` / ``project_key`` restrict aggregates to one platform scope (omit both for global).
    """
    now = int(time.time())
    win = max(60, int(finished_window_seconds))
    cutoff = now - win
    scope_frag, scope_args = _jobs_scope_sql_fragment(namespace, project_key, table_alias=None)
    inflight_rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(provider), ''), '_default') AS p, COUNT(*) AS c
        FROM jobs
        WHERE status IN ('CLAIMED', 'PREPROCESSING', 'INFERENCING', 'POSTPROCESSING')
          {scope_frag}
        GROUP BY 1
        """,
        tuple(scope_args),
    ).fetchall()
    inflight_by_provider = {str(r["p"]): int(r["c"]) for r in inflight_rows}
    fin_rows = conn.execute(
        f"""
        SELECT COALESCE(NULLIF(TRIM(provider), ''), '_default') AS p,
          COUNT(*) AS n,
          SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded,
          SUM(CASE WHEN status IN ('FAILED_PERMANENT', 'DEAD_LETTERED', 'CANCELLED') THEN 1 ELSE 0 END)
            AS failed_terminal,
          AVG(CASE WHEN COALESCE(inference_ms, 0) > 0 THEN inference_ms ELSE NULL END) AS avg_inference_ms,
          AVG(CASE WHEN COALESCE(total_latency_ms, 0) > 0 THEN total_latency_ms ELSE NULL END) AS avg_total_ms
        FROM jobs
        WHERE finished_at IS NOT NULL AND finished_at >= ?
          {scope_frag}
        GROUP BY 1
        """,
        (cutoff, *scope_args),
    ).fetchall()
    finished_stats_by_provider: dict[str, Any] = {}
    for r in fin_rows:
        pk = str(r["p"])
        ai = r["avg_inference_ms"]
        at = r["avg_total_ms"]
        finished_stats_by_provider[pk] = {
            "n": int(r["n"] or 0),
            "succeeded": int(r["succeeded"] or 0),
            "failed_terminal": int(r["failed_terminal"] or 0),
            "avg_inference_ms": float(ai) if ai is not None else None,
            "avg_total_latency_ms": float(at) if at is not None else None,
        }
    out: dict[str, Any] = {
        "now": now,
        "finished_window_seconds": win,
        "finished_since_unix": cutoff,
        "inflight_by_provider": inflight_by_provider,
        "finished_stats_by_provider": finished_stats_by_provider,
    }
    if namespace is not None or project_key is not None:
        out["scope_filter"] = {
            "namespace": _coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE)
            if namespace is not None
            else None,
            "project_key": _coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY)
            if project_key is not None
            else None,
        }
    return out


def _sha256_file(path: str, *, max_bytes: int = 64 * 1024 * 1024) -> str | None:
    """Best-effort content digest for local artifact files (skip missing/huge)."""
    try:
        p = Path(path)
        if not p.is_file():
            return None
        size = int(p.stat().st_size)
        if size <= 0 or size > max_bytes:
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _artifact_row_to_public(row: sqlite3.Row) -> dict[str, Any]:
    """Normalize a registry row for API / executor consumers."""
    meta: dict[str, Any] = {}
    raw = row["metadata_json"]
    if raw:
        try:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = {}
    jid = row["job_event_id"]
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    digest = None
    if "content_digest" in keys:
        digest = row["content_digest"]
    out: dict[str, Any] = {
        "artifact_id": int(row["id"]),
        "job_id": int(row["job_id"]),
        "kind": str(row["kind"]),
        "path": str(row["path"]),
        "generated_at": int(row["generated_at"]),
        "metadata": meta,
        "is_primary": bool(int(row["is_primary"] or 0)),
        "stage": row["stage"],
        "source": row["source"],
        "job_event_id": int(jid) if jid is not None else None,
        "created_at": int(row["created_at"]) if row["created_at"] is not None else None,
        "content_digest": str(digest) if digest else None,
    }
    cat = meta.get("category")
    if cat is not None:
        out["category"] = cat
    tax = meta.get("taxonomy")
    if tax is not None:
        out["taxonomy"] = tax
    role = meta.get("role")
    if role is not None:
        out["role"] = role
    return out


def list_artifacts_for_job(conn: sqlite3.Connection, *, job_id: int) -> list[dict[str, Any]]:
    """Return artifact registry rows for ``job_id`` (empty if table missing or none)."""
    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts' LIMIT 1"
    ).fetchone()
    if t is None:
        return []
    rows = conn.execute(
        """
        SELECT id, job_id, kind, path, generated_at, metadata_json, is_primary,
               stage, source, job_event_id, created_at, content_digest
        FROM artifacts
        WHERE job_id = ?
        ORDER BY is_primary DESC, id ASC
        """,
        (int(job_id),),
    ).fetchall()
    return [_artifact_row_to_public(r) for r in rows]


def sync_job_artifacts_from_success_event(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    job_event_id: int,
    payload: dict[str, Any] | None,
) -> None:
    """
    Replace registry rows for ``job_id`` from a SUCCEEDED event payload.

    **Timeline / audit:** ``job_events`` (especially ``to_status = 'SUCCEEDED'``) holds the verbatim JSON
    snapshot including ``artifacts`` and ``primary_artifact``. Each inserted ``artifacts`` row sets
    ``job_event_id`` to that event so UIs can anchor outputs on the same ledger entry that closed the job.

    **Primary flag:** :func:`services.job_artifacts.select_primary_artifact` defines SSOT ordering; we also
    accept a matching ``payload["primary_artifact"]`` when present. If no row matches, fall back to the first
    ``analysis_results_json`` row, then the first row.

    **metadata_json:** Stores per-artifact extras not lifted to typed columns (``category``, ``taxonomy``,
    ``role``, forward-compatible fields). Top-level ``kind`` / ``path`` remain columns for indexing.
    """
    from services.job_artifacts import KIND_ANALYSIS_RESULTS, select_primary_artifact

    t = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='artifacts' LIMIT 1"
    ).fetchone()
    if t is None:
        return
    jid = int(job_id)
    conn.execute("DELETE FROM artifacts WHERE job_id = ?", (jid,))
    if not isinstance(payload, dict):
        return
    arts = payload.get("artifacts")
    if not isinstance(arts, list) or not arts:
        return
    norm = [a for a in arts if isinstance(a, dict)]
    expected = select_primary_artifact(norm)
    declared = payload.get("primary_artifact") if isinstance(payload.get("primary_artifact"), dict) else None
    if expected is not None and declared is not None:
        dk = str(declared.get("kind") or "").strip()
        dp = str(declared.get("path") or "").strip()
        ek = str(expected.get("kind") or "").strip()
        ep = str(expected.get("path") or "").strip()
        if dk != ek or dp != ep:
            declared = None
    primary_ref = expected if expected is not None else declared
    pk = str(primary_ref.get("kind") or "") if primary_ref else ""
    pp = str(primary_ref.get("path") or "") if primary_ref else ""
    now = int(time.time())
    # Indexed columns + JSON snapshot split: anything else (taxonomy, role, category, …) → metadata_json
    core_keys = frozenset({"kind", "path", "generated_at", "stage", "source", "content_digest"})
    for a in arts:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "").strip()
        path = str(a.get("path") or "").strip()
        if not kind or not path:
            continue
        try:
            gen = int(a.get("generated_at") or now)
        except (TypeError, ValueError):
            gen = now
        stage = a.get("stage")
        src = a.get("source")
        stage_s = str(stage).strip() if stage not in (None, "") else None
        src_s = str(src).strip() if src not in (None, "") else None
        meta = {k: v for k, v in a.items() if k not in core_keys}
        meta_json = json.dumps(meta, ensure_ascii=False) if meta else None
        is_primary = 1 if (pk and pp and kind == pk and path == pp) else 0
        digest = a.get("content_digest")
        digest_s = str(digest).strip() if digest not in (None, "") else None
        if digest_s is None and (is_primary or kind == KIND_ANALYSIS_RESULTS):
            digest_s = _sha256_file(path)
        conn.execute(
            """
            INSERT INTO artifacts (
              job_id, kind, path, generated_at, metadata_json, is_primary,
              stage, source, job_event_id, created_at, content_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                jid,
                kind,
                path,
                gen,
                meta_json,
                is_primary,
                stage_s,
                src_s,
                int(job_event_id),
                now,
                digest_s,
            ),
        )
    if pk and pp:
        hit = conn.execute(
            "SELECT 1 FROM artifacts WHERE job_id = ? AND is_primary = 1 LIMIT 1",
            (jid,),
        ).fetchone()
        if hit is not None:
            return
    conn.execute(
        """
        UPDATE artifacts SET is_primary = 0 WHERE job_id = ?
        """,
        (jid,),
    )
    row = conn.execute(
        """
        SELECT id FROM artifacts
        WHERE job_id = ? AND kind = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (jid, KIND_ANALYSIS_RESULTS),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT id FROM artifacts WHERE job_id = ? ORDER BY id ASC LIMIT 1",
            (jid,),
        ).fetchone()
    if row is not None:
        conn.execute(
            "UPDATE artifacts SET is_primary = 1 WHERE id = ?",
            (int(row["id"]),),
        )


def enrich_job_with_success_artifacts(conn: sqlite3.Connection, job: dict[str, Any]) -> dict[str, Any]:
    """
    Copy job row and add ``output_artifacts`` / ``primary_artifact`` (+ optional ``artifact_lineage``).

    Prefers the ``artifacts`` registry when populated; otherwise falls back to the latest
    ``SUCCEEDED`` ``job_events.payload_json`` (legacy rows / pre-registry DBs).

    ``artifact_lineage`` summarizes how outputs tie to ``job_events`` (registry rows carry
    ``job_event_id`` pointing at the success event written in the same transaction as the sync).
    """
    if str(job.get("status") or "") != "SUCCEEDED":
        return dict(job)
    out = dict(job)
    jid = int(job["id"])
    registry = list_artifacts_for_job(conn, job_id=jid)
    if registry:
        out["output_artifacts"] = registry
        prim = next((x for x in registry if x.get("is_primary")), None)
        if prim is not None:
            out["primary_artifact"] = prim
        ev_ids = sorted(
            {int(x["job_event_id"]) for x in registry if x.get("job_event_id") is not None}
        )
        out["artifact_lineage"] = {
            "source": "artifacts_table",
            "artifact_count": len(registry),
            "provenance_job_event_ids": ev_ids,
            "primary_artifact_id": (prim or {}).get("artifact_id") if isinstance(prim, dict) else None,
        }
        return out

    row = conn.execute(
        """
        SELECT id, payload_json FROM job_events
        WHERE job_id = ? AND to_status = 'SUCCEEDED'
        ORDER BY id DESC
        LIMIT 1
        """,
        (jid,),
    ).fetchone()
    if row is None or not row["payload_json"]:
        return out
    try:
        p = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        return out
    if not isinstance(p, dict):
        return out
    if isinstance(p.get("artifacts"), list):
        out["output_artifacts"] = p["artifacts"]
    if isinstance(p.get("primary_artifact"), dict):
        out["primary_artifact"] = p["primary_artifact"]
    out["artifact_lineage"] = {
        "source": "job_events_payload",
        "provenance_job_event_ids": [int(row["id"])],
        "artifact_count": len(p["artifacts"]) if isinstance(p.get("artifacts"), list) else 0,
        "primary_artifact_id": None,
    }
    return out


def list_jobs(
    conn: sqlite3.Connection,
    *,
    statuses: list[str] | None = None,
    job_type: str | None = None,
    worker_id: int | None = None,
    session_id: int | None = None,
    photo_id: int | None = None,
    root_job_id: int | None = None,
    trace_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
    sort: str = "created_at",
    namespace: str | None = None,
    project_key: str | None = None,
) -> list[dict[str, Any]]:
    """List jobs with optional filters.

    ``sort``: ``created_at`` (default, descending) or ``updated_at`` (descending).
    If ``namespace`` and/or ``project_key`` are set, filter to that scope (use ``'default'`` to match
    only legacy / unset semantic bucket).
    """
    clauses: list[str] = []
    args: list[Any] = []
    if statuses:
        qm = ",".join("?" * len(statuses))
        clauses.append(f"status IN ({qm})")
        args.extend(statuses)
    if job_type is not None:
        clauses.append("job_type = ?")
        args.append(job_type)
    if namespace is not None:
        clauses.append("namespace = ?")
        args.append(_coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE))
    if project_key is not None:
        clauses.append("project_key = ?")
        args.append(_coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY))
    if worker_id is not None:
        clauses.append("worker_id = ?")
        args.append(worker_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        args.append(session_id)
    if photo_id is not None:
        clauses.append("photo_id = ?")
        args.append(photo_id)
    if root_job_id is not None:
        clauses.append("root_job_id = ?")
        args.append(root_job_id)
    if trace_id is not None and str(trace_id).strip():
        clauses.append("trace_id LIKE ?")
        args.append(f"%{str(trace_id).strip()}%")
    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_col = "updated_at" if sort == "updated_at" else "created_at"
    args.extend([max(1, limit), max(0, offset)])
    rows = conn.execute(
        f"""
        SELECT *
        FROM jobs
        {where_sql}
        ORDER BY {order_col} DESC, id DESC
        LIMIT ? OFFSET ?
        """,
        args,
    ).fetchall()
    return [dict(r) for r in rows]


def list_jobs_for_stage_group(
    conn: sqlite3.Connection,
    *,
    root_job_id: int,
) -> list[dict[str, Any]]:
    """All rows in a stage group: ``root_job_id`` matches, or the root row id itself. Sorted by ``stage_order``."""
    r = int(root_job_id)
    rows = conn.execute(
        """
        SELECT * FROM jobs
        WHERE id = ? OR root_job_id = ?
        ORDER BY COALESCE(stage_order, 0) ASC, id ASC
        """,
        (r, r),
    ).fetchall()
    return [dict(x) for x in rows]


def create_linear_staged_session_jobs(
    conn: sqlite3.Connection,
    *,
    session_id: int,
    trace_id: str | None = None,
    config_path: str = "configs/livehouse.yaml",
    priority: int = 0,
    namespace: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    """
    Create a **linear** chain of ``PIPELINE_STAGE`` jobs for a session: each stage
    ``depends_on`` the previous. First stage's ``root_job_id`` is set to its own id.

    Does **not** replace ``seed_analyze_session_jobs`` (monolithic ``ANALYZE_SESSION``). Opt-in
    for stage-aware execution experiments.
    """
    from services.pipeline_stages import CANONICAL_PIPELINE_STAGES, STAGE_JOB_TYPE

    row = conn.execute(
        "SELECT id, previews_dir FROM sessions WHERE id = ?",
        (int(session_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"session not found: {session_id}")
    previews_dir = str(row["previews_dir"] or "").strip()
    if not previews_dir:
        raise ValueError(f"session {session_id} has empty previews_dir")
    stage_ids: list[int] = []
    prev_id: int | None = None
    root_id: int | None = None
    for order, st_name in enumerate(CANONICAL_PIPELINE_STAGES):
        base_payload = {
            "config_path": config_path,
            "execution_mode": "staged_pipeline",
            "source_dir": previews_dir,
            "stage_name": st_name,
            "stage_order": order,
        }
        jid = create_job(
            conn,
            job_type=STAGE_JOB_TYPE,
            session_id=int(session_id),
            trace_id=trace_id,
            priority=priority,
            payload=base_payload,
            root_job_id=root_id,
            parent_job_id=prev_id,
            stage_name=st_name,
            stage_order=order,
            depends_on_job_id=prev_id,
            is_stage=1,
            namespace=namespace,
            project_key=project_key,
        )
        if root_id is None:
            root_id = jid
            conn.execute("UPDATE jobs SET root_job_id = ? WHERE id = ?", (root_id, jid))
            conn.commit()
        stage_ids.append(jid)
        prev_id = jid
    return {
        "ok": True,
        "root_job_id": root_id,
        "session_id": int(session_id),
        "stage_job_ids": stage_ids,
        "stage_count": len(stage_ids),
    }


def claim_jobs(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
    job_type: str | None = None,
    limit: int = 1,
) -> list[dict[str, Any]]:
    """Atomically claim next runnable jobs in priority order."""
    if limit <= 0:
        return []
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        runnable_qm = _status_placeholders(_RUNNABLE_JOB_STATUSES)
        dep = _sql_dependency_satisfied()
        where = f"status IN ({runnable_qm}) AND attempt < max_attempts AND {dep}"
        args: list[Any] = list(_RUNNABLE_JOB_STATUSES)
        if job_type is not None:
            where += " AND job_type = ?"
            args.append(job_type)
        fetch_cap = min(500, max(limit * 100, limit))
        args.append(fetch_cap)
        rows = conn.execute(
            f"""
            SELECT id, status, enqueued_at, job_type, stage_name, payload_json
            FROM jobs
            WHERE {where}
            ORDER BY priority DESC, enqueued_at ASC, id ASC
            LIMIT ?
            """,
            args,
        ).fetchall()
        if not rows:
            conn.commit()
            return []

        claimed_job_ids: list[int] = []
        for row in rows:
            if len(claimed_job_ids) >= limit:
                break
            adm = worker_runtime_admission(conn, worker_id=worker_id)
            if not adm["ok"]:
                break
            job_id = int(row["id"])
            probe_d: dict[str, Any] = {
                "id": job_id,
                "job_type": row["job_type"],
                "stage_name": row["stage_name"],
                "payload_json": row["payload_json"],
            }
            gate = worker_executor_claim_gate_for_job(conn, worker_id=worker_id, job_row=probe_d)
            if not gate["ok"]:
                continue
            prev_status = str(row["status"])
            enqueued_at = int(row["enqueued_at"] or now)
            queue_wait_ms = max(0, (now - enqueued_at) * 1000)
            updated = conn.execute(
                f"""
                UPDATE jobs
                SET status = 'CLAIMED',
                    worker_id = ?,
                    attempt = attempt + 1,
                    claim_generation = COALESCE(claim_generation, 0) + 1,
                    claimed_at = ?,
                    queue_wait_ms = ?,
                    updated_at = ?
                WHERE id = ? AND status IN ({runnable_qm}) AND attempt < max_attempts
                """,
                (worker_id, now, queue_wait_ms, now, job_id, *_RUNNABLE_JOB_STATUSES),
            ).rowcount
            if updated <= 0:
                continue
            append_job_event(
                conn,
                job_id=job_id,
                from_status=prev_status,
                to_status="CLAIMED",
                message="job claimed",
                payload={"worker_id": worker_id, "queue_wait_ms": queue_wait_ms},
            )
            claimed_job_ids.append(job_id)
        if not claimed_job_ids:
            conn.commit()
            return []
        qm = ",".join("?" * len(claimed_job_ids))
        claimed_rows = conn.execute(
            f"SELECT * FROM jobs WHERE id IN ({qm}) ORDER BY priority DESC, enqueued_at ASC, id ASC",
            claimed_job_ids,
        ).fetchall()
        conn.commit()
        return [dict(r) for r in claimed_rows]
    except Exception:
        conn.rollback()
        raise


def update_job_status(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    to_status: str,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    preprocess_ms: int | None = None,
    inference_ms: int | None = None,
    postprocess_ms: int | None = None,
    total_latency_ms: int | None = None,
    fence_claim_generation: int | None = None,
    fence_worker_id: int | None = None,
) -> None:
    """
    Update ``jobs.status`` (+ optional metrics / error columns) and append ``job_events``.

    **Transition policy:** this function intentionally does **not** reject “unexpected” ``from_status → to_status``
    pairs — operators, migrations, and future code paths may need flexibility. For the documented machine,
    see :data:`DOCUMENTED_PIPELINE_FORWARD_EDGES`, :data:`DOCUMENTED_MAINTENANCE_EDGES`, and
    :func:`job_transition_is_documented_ssot`.

    Note: ``started_at`` is seeded when entering ``PREPROCESSING|INFERENCING|POSTPROCESSING`` from
    ``QUEUED`` or ``CLAIMED``; production executors should **claim** before pipeline stages, but the SQL
    allows ``QUEUED → PREPROCESSING`` if a caller bypasses claim.

    When ``fence_claim_generation`` is set, the write is rejected with :class:`ClaimFenceError` unless the
    row is still an active pipeline status owned by that generation (and optional ``fence_worker_id``).
    """
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status, claim_generation, worker_id FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"job id not found: {job_id}")
        from_status = str(row["status"])
        if fence_claim_generation is not None:
            cur_gen = int(row["claim_generation"] or 0)
            if cur_gen != int(fence_claim_generation):
                raise ClaimFenceError(
                    f"job {job_id}: stale claim_generation "
                    f"(have={cur_gen}, fence={fence_claim_generation})"
                )
            if fence_worker_id is not None:
                cur_wid = row["worker_id"]
                if cur_wid is None or int(cur_wid) != int(fence_worker_id):
                    raise ClaimFenceError(
                        f"job {job_id}: stale worker_id fence "
                        f"(have={cur_wid}, fence={fence_worker_id})"
                    )
            if from_status not in JOB_STATUSES_ACTIVE_PIPELINE:
                raise ClaimFenceError(
                    f"job {job_id}: fenced write while status={from_status!r} (not active)"
                )

        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [to_status, now]
        if to_status in ("PREPROCESSING", "INFERENCING", "POSTPROCESSING") and from_status in ("QUEUED", "CLAIMED"):
            sets.append("started_at = COALESCE(started_at, ?)")
            vals.append(now)
        if to_status in ("SUCCEEDED", "FAILED_RETRYABLE", "FAILED_PERMANENT", "CANCELLED", "DEAD_LETTERED"):
            sets.append("finished_at = ?")
            vals.append(now)
        if error_code is not None:
            sets.append("error_code = ?")
            vals.append(error_code)
        if error_message is not None:
            sets.append("error_message = ?")
            vals.append(error_message)
        if preprocess_ms is not None:
            sets.append("preprocess_ms = ?")
            vals.append(preprocess_ms)
        if inference_ms is not None:
            sets.append("inference_ms = ?")
            vals.append(inference_ms)
        if postprocess_ms is not None:
            sets.append("postprocess_ms = ?")
            vals.append(postprocess_ms)
        if total_latency_ms is not None:
            sets.append("total_latency_ms = ?")
            vals.append(total_latency_ms)
        vals.append(job_id)
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", vals)
        ev_id = append_job_event(
            conn,
            job_id=job_id,
            from_status=from_status,
            to_status=to_status,
            message=message,
            payload=payload,
        )
        if to_status == "SUCCEEDED":
            sync_job_artifacts_from_success_event(
                conn, job_id=job_id, job_event_id=ev_id, payload=payload
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fail_job_retryable(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
    fence_claim_generation: int | None = None,
    fence_worker_id: int | None = None,
) -> str:
    """
    After a failing **execution**, move the row to ``FAILED_RETRYABLE`` so dispatch can reclaim it,
    unless the **claim budget** is exhausted — then ``DEAD_LETTERED``.

    Preconditions (caller contract): invoked while the row reflects an in-flight worker attempt that
    is ending in error (typically statuses in ``JOB_STATUSES_ACTIVE_PIPELINE``).

    Exhaustion rule (consistent with LIST/claim SQL filtering ``attempt < max_attempts``):

    - ``attempt`` counts successful claims (``JobLifecycle.claim`` / :func:`claim_jobs` increments it once per claim).
    - When ``fail_job_retryable`` runs immediately after attempt *N*, if ``N >= max_attempts`` **and**
      ``max_attempts > 0``, the SSOT transitions to ``DEAD_LETTERED`` (no further automatic claims).

    Misconfiguration guard: ``max_attempts <= 0`` cannot satisfy any claim predicate; callers should
    not create such rows, but when seen we dead-letter rather than looping ``FAILED_RETRYABLE`` forever.
    (:func:`reconcile_exhausted_retryable_to_dead_letter` still handles legacy stuck ``FAILED_RETRYABLE``.)

    Returns the status written: ``FAILED_RETRYABLE`` or ``DEAD_LETTERED``.
    """
    row = conn.execute(
        "SELECT attempt, max_attempts FROM jobs WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"job id not found: {job_id}")
    attempt = int(row["attempt"] or 0)
    max_attempts = int(row["max_attempts"] or 0)
    exhausted = False
    merge_extra: dict[str, Any]
    if max_attempts <= 0:
        exhausted = True
        merge_extra = {"reason": "invalid_max_attempts_non_positive", "attempt": attempt, "max_attempts": max_attempts}
    elif max_attempts > 0 and attempt >= max_attempts:
        exhausted = True
        merge_extra = {"reason": "max_attempts_exhausted", "attempt": attempt, "max_attempts": max_attempts}
    if exhausted:
        merged = dict(payload or {})
        merged.update(merge_extra)
        dl_message = (
            "job dead-lettered (invalid max_attempts configuration)"
            if max_attempts <= 0
            else "job dead-lettered (max attempts exhausted)"
        )
        fail_job_dead_lettered(
            conn,
            job_id=job_id,
            error_code=error_code,
            error_message=error_message,
            payload=merged,
            message=dl_message,
            fence_claim_generation=fence_claim_generation,
            fence_worker_id=fence_worker_id,
        )
        return "DEAD_LETTERED"
    update_job_status(
        conn,
        job_id=job_id,
        to_status="FAILED_RETRYABLE",
        message="job failed (retryable)",
        payload=payload,
        error_code=error_code,
        error_message=error_message,
        fence_claim_generation=fence_claim_generation,
        fence_worker_id=fence_worker_id,
    )
    return "FAILED_RETRYABLE"


def fail_job_dead_lettered(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
    message: str | None = None,
    fence_claim_generation: int | None = None,
    fence_worker_id: int | None = None,
) -> None:
    """Terminal state: no automatic retries; requires human retry or new job."""
    update_job_status(
        conn,
        job_id=job_id,
        to_status="DEAD_LETTERED",
        message=message or "job dead-lettered (max attempts exhausted)",
        payload=payload,
        error_code=error_code,
        error_message=error_message,
        fence_claim_generation=fence_claim_generation,
        fence_worker_id=fence_worker_id,
    )


def fail_job_permanent(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    error_code: str | None = None,
    error_message: str | None = None,
    payload: dict[str, Any] | None = None,
    fence_claim_generation: int | None = None,
    fence_worker_id: int | None = None,
) -> None:
    """Mark job failed permanently."""
    update_job_status(
        conn,
        job_id=job_id,
        to_status="FAILED_PERMANENT",
        message="job failed (permanent)",
        payload=payload,
        error_code=error_code,
        error_message=error_message,
        fence_claim_generation=fence_claim_generation,
        fence_worker_id=fence_worker_id,
    )


def mark_job_succeeded(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    preprocess_ms: int | None = None,
    inference_ms: int | None = None,
    postprocess_ms: int | None = None,
    total_latency_ms: int | None = None,
    payload: dict[str, Any] | None = None,
    fence_claim_generation: int | None = None,
    fence_worker_id: int | None = None,
) -> None:
    """Mark job succeeded with optional latency metrics."""
    update_job_status(
        conn,
        job_id=job_id,
        to_status="SUCCEEDED",
        message="job succeeded",
        payload=payload,
        preprocess_ms=preprocess_ms,
        inference_ms=inference_ms,
        postprocess_ms=postprocess_ms,
        total_latency_ms=total_latency_ms,
        fence_claim_generation=fence_claim_generation,
        fence_worker_id=fence_worker_id,
    )


def inference_request_payload_hash(
    *,
    prompt: str,
    image_path: str,
    model_name: str | None = None,
) -> str:
    """SHA-256 hex digest for audit/dedup (prompt + image path + model hint)."""
    raw = f"{model_name or ''}\x00{prompt}\x00{image_path}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def create_model_run(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    provider: str,
    model_name: str | None = None,
    request_payload_hash: str | None = None,
    primary_provider: str | None = None,
    fallback_provider: str | None = None,
    primary_model: str | None = None,
    prompt_length: int | None = None,
) -> int:
    """
    Insert a ``QUEUED`` model run row; returns ``model_runs.id``.

    ``provider`` / ``model_name`` mirror the requested route (compat); ``primary_*`` are the
    explicit router snapshot for analytics.
    """
    now = int(time.time())
    pp = (primary_provider or provider or "").strip() or None
    fb = (fallback_provider or "").strip() or None
    pm = (primary_model or model_name or "").strip() or None
    conn.execute("BEGIN IMMEDIATE")
    try:
        hit = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        if hit is None:
            raise ValueError(
                f"Cannot create model_run: jobs.id={int(job_id)} does not exist "
                "(foreign key target missing; wrong id, wrong LUMA_BRAIN_DB, or uncommitted job row)"
            )
        cur = conn.execute(
            """
            INSERT INTO model_runs (
                job_id, provider, model_name, primary_provider, fallback_provider, primary_model,
                request_payload_hash, status, degraded, fallback_used, prompt_length, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', 0, 0, ?, ?)
            """,
            (job_id, provider, model_name, pp, fb, pm, request_payload_hash, prompt_length, now),
        )
        rid = int(cur.lastrowid)
        conn.commit()
        _log = logging.getLogger(__name__)
        _log.debug(
            "luma_brain tx committed create_model_run run_id=%s job_id=%s",
            rid,
            int(job_id),
        )
        return rid
    except Exception:
        conn.rollback()
        raise


def create_model_run_and_mark_started(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    provider: str,
    model_name: str | None = None,
    request_payload_hash: str | None = None,
    primary_provider: str | None = None,
    fallback_provider: str | None = None,
    primary_model: str | None = None,
    prompt_length: int | None = None,
    queue_wait_ms: int | None = None,
) -> int:
    """Insert ``model_runs`` as ``QUEUED`` then ``STARTED`` in **one** transaction; returns ``id``.

    Avoids a window where another process only sees ``QUEUED`` without ``STARTED``, and keeps
    parent ``jobs`` visibility consistent under ``BEGIN IMMEDIATE``.
    """
    now = int(time.time())
    pp = (primary_provider or provider or "").strip() or None
    fb = (fallback_provider or "").strip() or None
    pm = (primary_model or model_name or "").strip() or None
    _log = logging.getLogger(__name__)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _log.debug(
            "luma_brain tx begin create_model_run_and_mark_started job_id=%s thread=%s",
            int(job_id),
            threading.current_thread().name,
        )
        hit = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        if hit is None:
            raise ValueError(
                f"Cannot create model_run: jobs.id={int(job_id)} does not exist "
                "(foreign key target missing; wrong id, wrong LUMA_BRAIN_DB, or uncommitted job row)"
            )
        cur = conn.execute(
            """
            INSERT INTO model_runs (
                job_id, provider, model_name, primary_provider, fallback_provider, primary_model,
                request_payload_hash, status, degraded, fallback_used, prompt_length, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'QUEUED', 0, 0, ?, ?)
            """,
            (job_id, provider, model_name, pp, fb, pm, request_payload_hash, prompt_length, now),
        )
        rid = int(cur.lastrowid)
        if queue_wait_ms is not None:
            conn.execute(
                """
                UPDATE model_runs
                SET status = 'STARTED',
                    queue_wait_ms = ?
                WHERE id = ?
                """,
                (max(0, int(queue_wait_ms)), rid),
            )
        else:
            conn.execute(
                "UPDATE model_runs SET status = 'STARTED' WHERE id = ?",
                (rid,),
            )
        conn.commit()
        _log.debug(
            "luma_brain tx committed create_model_run_and_mark_started run_id=%s job_id=%s",
            rid,
            int(job_id),
        )
        return rid
    except Exception:
        conn.rollback()
        _log.debug(
            "luma_brain tx rollback create_model_run_and_mark_started job_id=%s",
            int(job_id),
        )
        raise


def mark_model_run_started(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    queue_wait_ms: int | None = None,
) -> None:
    """Transition to ``STARTED`` and optionally set ``queue_wait_ms`` (time from enqueue to worker start)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if queue_wait_ms is not None:
            conn.execute(
                """
                UPDATE model_runs
                SET status = 'STARTED',
                    queue_wait_ms = ?
                WHERE id = ?
                """,
                (max(0, int(queue_wait_ms)), run_id),
            )
        else:
            conn.execute(
                "UPDATE model_runs SET status = 'STARTED' WHERE id = ?",
                (run_id,),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mark_model_run_succeeded(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    latency_ms: int,
    degraded: int = 0,
    model_name: str | None = None,
    # Extended ledger (optional; all no-op if omitted by older callers)
    end_to_end_latency_ms: int | None = None,
    provider_latency_ms: int | None = None,
    final_model: str | None = None,
    primary_provider: str | None = None,
    fallback_provider: str | None = None,
    primary_model: str | None = None,
    fallback_used: int = 0,
    response_length: int | None = None,
    outcome_attribution: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
) -> None:
    """
    Mark ``SUCCEEDED``.

    ``latency_ms`` and ``end_to_end_latency_ms`` both store E2E wall time (queue worker ``infer()``) for
    backward compatibility; ``provider_latency_ms`` is the sum of primary (+ optional fallback) hops.
    ``*_tokens`` are best-effort provider usage counts (NULL when the backend does not report them);
    ``total_tokens`` defaults to ``prompt + completion`` when only the two parts are given.
    """
    e2e = end_to_end_latency_ms if end_to_end_latency_ms is not None else latency_ms
    prov_ms = provider_latency_ms if provider_latency_ms is not None else e2e
    pt = int(prompt_tokens) if prompt_tokens is not None else None
    ct = int(completion_tokens) if completion_tokens is not None else None
    tt = int(total_tokens) if total_tokens is not None else None
    if tt is None and (pt is not None or ct is not None):
        tt = (pt or 0) + (ct or 0)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE model_runs
            SET status = 'SUCCEEDED',
                latency_ms = ?,
                end_to_end_latency_ms = ?,
                provider_latency_ms = ?,
                degraded = ?,
                fallback_used = ?,
                model_name = COALESCE(?, model_name),
                final_model = COALESCE(?, final_model),
                primary_provider = COALESCE(?, primary_provider),
                fallback_provider = COALESCE(?, fallback_provider),
                primary_model = COALESCE(?, primary_model),
                response_length = COALESCE(?, response_length),
                prompt_tokens = COALESCE(?, prompt_tokens),
                completion_tokens = COALESCE(?, completion_tokens),
                total_tokens = COALESCE(?, total_tokens),
                outcome_attribution = COALESCE(?, outcome_attribution)
            WHERE id = ?
            """,
            (
                max(0, int(latency_ms)),
                max(0, int(e2e)),
                max(0, int(prov_ms)),
                1 if degraded else 0,
                1 if fallback_used else 0,
                model_name,
                final_model,
                primary_provider,
                fallback_provider,
                primary_model,
                response_length,
                pt,
                ct,
                tt,
                outcome_attribution,
                run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mark_model_run_failed(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    latency_ms: int | None = None,
    error_message: str | None = None,
    degraded: int = 0,
    model_name: str | None = None,
    status: str = "FAILED",
    end_to_end_latency_ms: int | None = None,
    provider_latency_ms: int | None = None,
    final_model: str | None = None,
    error_type: str | None = None,
    primary_provider: str | None = None,
    fallback_provider: str | None = None,
    primary_model: str | None = None,
    fallback_used: int = 0,
    response_length: int | None = None,
    outcome_attribution: str | None = None,
) -> None:
    """Mark terminal failure (``FAILED``, ``TIMEOUT``, or ``CANCELLED``)."""
    if status not in ("FAILED", "TIMEOUT", "CANCELLED"):
        raise ValueError(f"invalid model_run terminal status: {status}")
    msg = (error_message or "")[:2000] if error_message else None
    e2e = end_to_end_latency_ms if end_to_end_latency_ms is not None else latency_ms
    prov_ms = (
        int(provider_latency_ms) if provider_latency_ms is not None else (int(e2e) if e2e is not None else None)
    )
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            UPDATE model_runs
            SET status = ?,
                latency_ms = ?,
                end_to_end_latency_ms = ?,
                provider_latency_ms = ?,
                degraded = ?,
                fallback_used = ?,
                error_message = ?,
                error_type = ?,
                model_name = COALESCE(?, model_name),
                final_model = COALESCE(?, final_model),
                primary_provider = COALESCE(?, primary_provider),
                fallback_provider = COALESCE(?, fallback_provider),
                primary_model = COALESCE(?, primary_model),
                response_length = COALESCE(?, response_length),
                outcome_attribution = COALESCE(?, outcome_attribution)
            WHERE id = ?
            """,
            (
                status,
                max(0, int(latency_ms)) if latency_ms is not None else None,
                max(0, int(e2e)) if e2e is not None else None,
                max(0, int(prov_ms)) if prov_ms is not None else None,
                1 if degraded else 0,
                1 if fallback_used else 0,
                msg,
                error_type,
                model_name,
                final_model,
                primary_provider,
                fallback_provider,
                primary_model,
                response_length,
                outcome_attribution,
                run_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def replace_model_run_attempts(
    conn: sqlite3.Connection,
    *,
    model_run_id: int,
    attempts: list[dict[str, Any]],
) -> None:
    """Replace all attempt rows for a model run (ordered ``seq`` = list index)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM model_run_attempts WHERE model_run_id = ?", (int(model_run_id),))
        for i, a in enumerate(attempts):
            conn.execute(
                """
                INSERT INTO model_run_attempts (
                    model_run_id, seq, role, provider_id, model_name, latency_ms,
                    ok, error_type, error_message, primary_skipped
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(model_run_id),
                    i,
                    str(a.get("role") or "primary"),
                    str(a.get("provider_id") or "unknown"),
                    a.get("model_name"),
                    max(0, int(a.get("latency_ms") or 0)),
                    1 if a.get("ok") else 0,
                    a.get("error_type"),
                    (str(a.get("error_message") or ""))[:2000] or None,
                    1 if a.get("primary_skipped") else 0,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def list_model_run_attempts_for_runs(
    conn: sqlite3.Connection,
    *,
    run_ids: list[int],
) -> dict[int, list[dict[str, Any]]]:
    """Map ``model_run_id`` → ordered attempt dicts (booleans normalized for JSON)."""
    if not run_ids:
        return {}
    cap_ids = [int(x) for x in run_ids]
    qm = ",".join("?" * len(cap_ids))
    rows = conn.execute(
        f"""
        SELECT id, model_run_id, seq, role, provider_id, model_name, latency_ms,
               ok, error_type, error_message, primary_skipped, created_at
        FROM model_run_attempts
        WHERE model_run_id IN ({qm})
        ORDER BY model_run_id ASC, seq ASC
        """,
        cap_ids,
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        d = dict(r)
        rid = int(d.pop("model_run_id"))
        d["ok"] = bool(d.get("ok"))
        d["primary_skipped"] = bool(d.get("primary_skipped"))
        out.setdefault(rid, []).append(d)
    return out


def list_model_runs_for_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return recent ``model_runs`` rows for a job (newest first)."""
    cap = max(1, min(1000, int(limit)))
    rows = conn.execute(
        """
        SELECT * FROM model_runs
        WHERE job_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(job_id), cap),
    ).fetchall()
    return [dict(r) for r in rows]


def summarize_model_run_costs(
    conn: sqlite3.Connection,
    *,
    since_ts: int | None = None,
    input_usd_per_mtok: float = 0.0,
    output_usd_per_mtok: float = 0.0,
    group_by_model: bool = False,
) -> list[dict[str, Any]]:
    """
    Aggregate token usage / latency / cost across ``model_runs`` for cost-per-inference reporting.

    Cost is derived (not stored per row) from token counts and the supplied per-million-token prices,
    so re-pricing never requires a backfill. Returns one ``{"final_model"?, runs, succeeded, tokens,
    avg/p95 latency, est cost}`` summary row (or one per ``final_model`` when ``group_by_model``).
    Rows with NULL token columns contribute to counts/latency but not to token/cost sums.
    """
    where = "status = 'SUCCEEDED'"
    params: list[Any] = []
    if since_ts is not None:
        where += " AND created_at >= ?"
        params.append(int(since_ts))

    in_price = max(0.0, float(input_usd_per_mtok))
    out_price = max(0.0, float(output_usd_per_mtok))

    def _summarize(label: str | None, run_rows: list[sqlite3.Row]) -> dict[str, Any]:
        n = len(run_rows)
        lat = sorted(int(r["end_to_end_latency_ms"] or 0) for r in run_rows)
        prompt_tok = sum(int(r["prompt_tokens"] or 0) for r in run_rows)
        completion_tok = sum(int(r["completion_tokens"] or 0) for r in run_rows)
        with_tokens = sum(1 for r in run_rows if r["total_tokens"] is not None)
        cost = (prompt_tok / 1_000_000.0) * in_price + (completion_tok / 1_000_000.0) * out_price
        out: dict[str, Any] = {
            "runs": n,
            "runs_with_token_usage": with_tokens,
            "prompt_tokens": prompt_tok,
            "completion_tokens": completion_tok,
            "total_tokens": prompt_tok + completion_tok,
            "avg_latency_ms": int(sum(lat) / n) if n else 0,
            "p95_latency_ms": _percentile_int(lat, 95),
            "est_cost_usd": round(cost, 6),
            "est_cost_per_1k_usd": round((cost / n) * 1000.0, 6) if n else 0.0,
            "avg_completion_tokens": round(completion_tok / with_tokens, 1) if with_tokens else 0.0,
        }
        if label is not None:
            out = {"final_model": label, **out}
        return out

    if group_by_model:
        rows = conn.execute(
            f"SELECT * FROM model_runs WHERE {where} ORDER BY final_model ASC, id ASC",
            params,
        ).fetchall()
        buckets: dict[str, list[sqlite3.Row]] = {}
        for r in rows:
            buckets.setdefault(str(r["final_model"] or r["model_name"] or "unknown"), []).append(r)
        return [_summarize(model, rs) for model, rs in sorted(buckets.items())]

    rows = conn.execute(f"SELECT * FROM model_runs WHERE {where}", params).fetchall()
    return [_summarize(None, rows)]


def _percentile_int(sorted_values: list[int], p: float) -> int:
    """Nearest-rank percentile over a pre-sorted list (0 when empty)."""
    if not sorted_values:
        return 0
    if len(sorted_values) == 1:
        return int(sorted_values[0])
    k = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return int(sorted_values[k])


def register_or_update_worker(
    conn: sqlite3.Connection,
    *,
    worker_name: str,
    worker_type: str = "generic",
    status: str = "ONLINE",
    capacity: int = 1,
) -> int:
    """Create worker row if missing; otherwise refresh mutable attributes."""
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id FROM workers WHERE worker_name = ?", (worker_name,)).fetchone()
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO workers (
                    worker_name, worker_type, status, last_heartbeat, capacity, inflight, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (worker_name, worker_type, status, now, max(1, capacity), now, now),
            )
            worker_id = int(cur.lastrowid)
        else:
            worker_id = int(row["id"])
            current = conn.execute(
                "SELECT status FROM workers WHERE id = ?",
                (worker_id,),
            ).fetchone()
            cur_status = str(current["status"]) if current and current["status"] is not None else status
            # Do not let a reconnecting worker process overwrite operator pause/drain/error (control plane).
            effective = status
            if cur_status in WORKER_STATUS_CONTROL_BLOCK_HEARTBEAT:
                effective = cur_status
            conn.execute(
                """
                UPDATE workers
                SET worker_type = ?,
                    status = ?,
                    capacity = ?,
                    last_heartbeat = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (worker_type, effective, max(1, capacity), now, now, worker_id),
            )
        conn.commit()
        return worker_id
    except Exception:
        conn.rollback()
        raise


def heartbeat_worker(
    conn: sqlite3.Connection,
    *,
    worker_id: int,
    worker_name: str | None = None,
    worker_type: str | None = None,
    inflight: int | None = None,
    status: str | None = None,
) -> None:
    """Refresh worker heartbeat (+ optional worker identity/inflight/status)."""
    now = int(time.time())
    apply_status = status
    if status == "ONLINE":
        cur = conn.execute("SELECT status FROM workers WHERE id = ?", (int(worker_id),)).fetchone()
        if cur is not None and str(cur["status"] or "") in WORKER_STATUS_CONTROL_BLOCK_HEARTBEAT:
            apply_status = None
    sets = ["last_heartbeat = ?", "updated_at = ?"]
    vals: list[Any] = [now, now]
    if worker_name is not None:
        sets.append("worker_name = ?")
        vals.append(worker_name)
    if worker_type is not None:
        sets.append("worker_type = ?")
        vals.append(worker_type)
    if inflight is not None:
        sets.append("inflight = ?")
        vals.append(max(0, inflight))
    if apply_status is not None:
        sets.append("status = ?")
        vals.append(apply_status)
    vals.append(worker_id)
    conn.execute(f"UPDATE workers SET {', '.join(sets)} WHERE id = ?", vals)
    conn.commit()


def mark_stale_workers_offline(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int | None = None,
) -> list[int]:
    """
    Mark ``ONLINE`` workers with stale heartbeats as ``OFFLINE``.

    Does not touch operator-controlled rows (``PAUSED`` / ``DRAINING`` / ``ERROR``).
    Returns affected worker ids.
    """
    import os

    if stale_after_seconds is None:
        raw = os.environ.get("LIVEHOUSE_WORKER_OFFLINE_AFTER_SECONDS", "180").strip()
        try:
            stale_after_seconds = max(60, int(raw))
        except ValueError:
            stale_after_seconds = 180
    if stale_after_seconds <= 0:
        return []
    now = int(time.time())
    cutoff = now - stale_after_seconds
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            """
            SELECT id FROM workers
            WHERE status = 'ONLINE'
              AND (last_heartbeat IS NULL OR last_heartbeat < ?)
            """,
            (cutoff,),
        ).fetchall()
        ids = [int(r["id"]) for r in rows]
        if ids:
            qm = ",".join("?" * len(ids))
            conn.execute(
                f"""
                UPDATE workers
                SET status = 'OFFLINE', updated_at = ?
                WHERE id IN ({qm})
                """,
                [now, *ids],
            )
        conn.commit()
        return ids
    except Exception:
        conn.rollback()
        raise


def requeue_stuck_jobs(
    conn: sqlite3.Connection,
    *,
    stale_after_seconds: int,
    worker_stale_after_seconds: int | None = None,
    limit: int = 100,
    reason: str = "stuck job requeued",
) -> list[int]:
    """
    Move stale active jobs back to QUEUED and clear worker claim.

    **Does not** increment ``attempt`` — this path recovers a **lost / abandoned claim**, not a failed
    execution retry (see module comment on ``attempt`` above).

    **Does** increment ``claim_generation`` so a zombie writer from the abandoned claim cannot
    terminate the row after requeue (see :class:`ClaimFenceError` / fenced ``mark_job_*``).

    Safety rule (minimal anti-false-positive):
    - Job must be in :data:`JOB_STATUSES_ACTIVE_PIPELINE`, with ``claimed_at`` older than
      ``stale_after_seconds``, **and**
    - Owning worker heartbeat must be stale past ``worker_stale_after_seconds`` (defaults to
      ``stale_after_seconds`` when omitted), **or** worker row missing / ``worker_id`` NULL.

    **Misclassification risk:** if heartbeats lag real liveness (long interval, network partition) or
    wall clocks skew, a still-running worker could be treated as dead — the row returns to ``QUEUED``
    while the old process might still finish. Fenced terminal writes reject that zombie; tune
    timeouts vs expected job duration and heartbeat frequency accordingly.
    """
    if stale_after_seconds <= 0 or limit <= 0:
        return []
    now = int(time.time())
    job_cutoff = now - stale_after_seconds
    worker_timeout = worker_stale_after_seconds if worker_stale_after_seconds is not None else stale_after_seconds
    if worker_timeout <= 0:
        return []
    worker_cutoff = now - worker_timeout
    conn.execute("BEGIN IMMEDIATE")
    try:
        active_qm = _status_placeholders(_ACTIVE_JOB_STATUSES)
        rows = conn.execute(
            f"""
            SELECT j.id, j.status, j.worker_id, w.last_heartbeat
            FROM jobs j
            LEFT JOIN workers w ON w.id = j.worker_id
            WHERE j.status IN ({active_qm})
              AND j.claimed_at IS NOT NULL
              AND j.claimed_at <= ?
              AND (
                j.worker_id IS NULL
                OR w.id IS NULL
                OR w.last_heartbeat IS NULL
                OR w.last_heartbeat <= ?
              )
            ORDER BY j.claimed_at ASC, j.id ASC
            LIMIT ?
            """,
            (*_ACTIVE_JOB_STATUSES, job_cutoff, worker_cutoff, limit),
        ).fetchall()
        if not rows:
            conn.commit()
            return []
        job_ids = [int(r["id"]) for r in rows]
        qm = ",".join("?" * len(job_ids))
        conn.execute(
            f"""
            UPDATE jobs
            SET status = 'QUEUED',
                worker_id = NULL,
                claimed_at = NULL,
                started_at = NULL,
                finished_at = NULL,
                claim_generation = COALESCE(claim_generation, 0) + 1,
                updated_at = ?
            WHERE id IN ({qm})
            """,
            (now, *job_ids),
        )
        for row in rows:
            payload: dict[str, Any] = {
                "stale_after_seconds": stale_after_seconds,
                "worker_stale_after_seconds": worker_timeout,
            }
            if row["worker_id"] is not None:
                payload["worker_id"] = int(row["worker_id"])
            if row["last_heartbeat"] is not None:
                payload["worker_last_heartbeat"] = int(row["last_heartbeat"])
            append_job_event(
                conn,
                job_id=int(row["id"]),
                from_status=str(row["status"]),
                to_status="QUEUED",
                message=reason,
                payload=payload,
            )
        conn.commit()
        return job_ids
    except Exception:
        conn.rollback()
        raise


def reconcile_exhausted_retryable_to_dead_letter(
    conn: sqlite3.Connection,
    *,
    limit: int = 500,
) -> list[int]:
    """
    Promote ``FAILED_RETRYABLE`` rows whose **claim budget is already exhausted**
    (``attempt >= max_attempts`` with ``max_attempts > 0``) to ``DEAD_LETTERED``.

    These can appear when older code paths wrote ``FAILED_RETRYABLE`` without running the same
    exhaustion guard as :func:`fail_job_retryable`, or when ``max_attempts`` was lowered after failures.

    Each promotion uses :func:`update_job_status` (emits ``job_events``). Safe to run periodically
    alongside :func:`requeue_stuck_jobs`.
    """
    if limit <= 0:
        return []
    rows = conn.execute(
        """
        SELECT id
        FROM jobs
        WHERE status = 'FAILED_RETRYABLE'
          AND attempt >= max_attempts
          AND max_attempts > 0
        ORDER BY id ASC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    promoted: list[int] = []
    for r in rows:
        jid = int(r["id"])
        update_job_status(
            conn,
            job_id=jid,
            to_status="DEAD_LETTERED",
            message="dead-lettered: retries exhausted (reconciled)",
            payload={"source": "reconcile_exhausted_retryable_to_dead_letter"},
        )
        promoted.append(jid)
    return promoted


def manual_retry_job(
    conn: sqlite3.Connection,
    *,
    job_id: int,
    source: str = "infra_api",
) -> dict[str, Any]:
    """
    Human-driven re-queue: ``QUEUED`` with a **fresh attempt budget** (``attempt`` reset to 0),
    worker claim cleared, and last error fields cleared. Emits a ``job_events`` row.

    Allowed from ``JOB_STATUSES_MANUAL_RETRY_ALLOWED_FROM`` (not from ``QUEUED`` or
    :data:`JOB_STATUSES_ACTIVE_PIPELINE`).
    """
    now = int(time.time())
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id, status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            conn.rollback()
            return {"ok": False, "job_id": job_id, "status": None, "message": "job not found"}
        cur = str(row["status"])
        if cur == "QUEUED" or cur in _ACTIVE_JOB_STATUSES:
            conn.rollback()
            return {
                "ok": True,
                "job_id": job_id,
                "status": cur,
                "message": "job already queued or active",
            }
        if cur not in JOB_STATUSES_MANUAL_RETRY_ALLOWED_FROM:
            conn.rollback()
            return {
                "ok": False,
                "job_id": job_id,
                "status": cur,
                "message": f"manual retry not supported from status {cur}",
            }
        conn.execute(
            """
            UPDATE jobs
            SET status = 'QUEUED',
                attempt = 0,
                worker_id = NULL,
                claimed_at = NULL,
                started_at = NULL,
                finished_at = NULL,
                error_code = NULL,
                error_message = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (now, job_id),
        )
        append_job_event(
            conn,
            job_id=job_id,
            from_status=cur,
            to_status="QUEUED",
            message="manual retry: reset attempts and re-queued",
            payload={"source": source, "attempt_reset": True},
        )
        conn.commit()
        return {
            "ok": True,
            "job_id": job_id,
            "status": "QUEUED",
            "message": "re-queued with attempt=0",
        }
    except Exception:
        conn.rollback()
        raise


def enqueue_job(
    conn: sqlite3.Connection,
    *,
    job_type: str,
    session_id: int | None = None,
    photo_id: int | None = None,
    priority: int = 0,
    max_attempts: int = 3,
    provider: str | None = None,
    model_name: str | None = None,
    trace_id: str | None = None,
    payload: dict[str, Any] | None = None,
    namespace: str | None = None,
    project_key: str | None = None,
) -> int:
    """Backward-compatible alias for create_job()."""
    return create_job(
        conn,
        job_type=job_type,
        session_id=session_id,
        photo_id=photo_id,
        priority=priority,
        max_attempts=max_attempts,
        provider=provider,
        model_name=model_name,
        trace_id=trace_id,
        payload=payload,
        namespace=namespace,
        project_key=project_key,
    )


def claim_job(
    conn: sqlite3.Connection,
    *,
    worker_id: int | None = None,
    job_type: str | None = None,
) -> dict[str, Any] | None:
    """Backward-compatible alias that claims exactly one job."""
    if worker_id is None:
        raise ValueError("worker_id is required for claim_job")
    claimed = claim_jobs(conn, worker_id=worker_id, job_type=job_type, limit=1)
    return claimed[0] if claimed else None
