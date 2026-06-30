"""
Centralized job state transitions against SSOT ``jobs`` / ``job_events``.

Intended **execution** path (see also ``utils.luma_brain`` job lifecycle constants —
``JOB_STATUSES_*``, :func:`job_transition_is_documented_ssot`, and attempt semantics in that module):

- **Runnable:** ``QUEUED`` or ``FAILED_RETRYABLE`` (dependency + ``attempt < max_attempts`` enforced in claim SQL).
- **Claim:** → ``CLAIMED`` (``attempt += 1``).
- **Pipeline:** ``PREPROCESSING`` → ``INFERENCING`` → optional ``POSTPROCESSING`` → ``SUCCEEDED`` via :meth:`succeed`.
- **Retryable failure:** :meth:`fail_retryable` → ``FAILED_RETRYABLE`` or ``DEAD_LETTERED`` when claim budget exhausted.
- **Permanent failure:** :meth:`fail_permanent` → ``FAILED_PERMANENT`` (from :func:`services.job_errors.classify_exception` in the main executor).

Operational paths (implemented in ``utils.luma_brain``): stuck active → ``QUEUED``, manual retry API → ``QUEUED``, cancel → ``CANCELLED``, reconcile exhausted retryable → ``DEAD_LETTERED``.

All pipeline *execution* status changes for ``tasks.run_job`` should go through this class so
orchestration stays decoupled from raw ``luma_brain`` calls and from ``photos.status``
(photos carry ingest + outcome only; see ``luma_brain_schema.sql`` / ``utils.luma_brain`` module doc).
"""
from __future__ import annotations

import time
from typing import Any

from celery.utils.log import get_task_logger

from utils.logging_context import make_log_extra

logger = get_task_logger(__name__)


class JobLifecycle:
    """DB-backed lifecycle helpers for a single open connection."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def claim(self, job_id: int, worker_id: int) -> tuple[dict[str, Any] | None, str | None]:
        """
        Atomically claim one runnable job row (QUEUED / FAILED_RETRYABLE) by primary key.

        Runnable guard matches :data:`utils.luma_brain.JOB_STATUSES_RUNNABLE` plus
        ``attempt < max_attempts`` and dependency satisfaction (see list/claim SQL).

        Enforces :func:`utils.luma_brain.worker_runtime_admission` in the same transaction
        (``ONLINE`` + live pipeline-active jobs on this worker < ``workers.capacity``).
        Live inflight counts ``jobs`` rows in ``CLAIMED|PREPROCESSING|INFERENCING|POSTPROCESSING``.

        Control-plane worker statuses (**DRAINING**, **PAUSED**, **ERROR**, **OFFLINE**) deny new claims;
        only **ONLINE** rows contribute pool headroom for dispatch — see ``worker_runtime_admission`` docstring.

        Returns ``(claimed, error_code)`` where ``error_code`` is ``worker_admission:*``,
        ``not_runnable``, or ``None`` on success.
        """
        from utils.luma_brain import append_job_event, worker_executor_claim_gate_for_job, worker_runtime_admission

        now = int(time.time())
        self._conn.execute("BEGIN")
        try:
            probe = self._conn.execute(
                """
                SELECT id, job_type, stage_name, payload_json, status FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
            if probe is None:
                self._conn.rollback()
                return None, "not_runnable"
            probe_d: dict[str, Any] = dict(probe)

            adm = worker_runtime_admission(self._conn, worker_id=worker_id)
            if not adm["ok"]:
                self._conn.rollback()
                reason = str(adm.get("reason") or "denied")
                logger.info(
                    "job claim blocked by worker admission",
                    extra=make_log_extra(
                        job_id=job_id,
                        worker_id=worker_id,
                        admission=reason,
                        status=adm.get("status"),
                    ),
                )
                return None, f"worker_admission:{reason}"

            gate = worker_executor_claim_gate_for_job(self._conn, worker_id=worker_id, job_row=probe_d)
            if not gate["ok"]:
                self._conn.rollback()
                reason = str(gate.get("reason") or "executor_denied")
                logger.info(
                    "job claim blocked by executor pool mismatch",
                    extra=make_log_extra(
                        job_id=job_id,
                        worker_id=worker_id,
                        admission=reason,
                        required_executor_class=gate.get("required_executor_class"),
                        worker_executor_pool=gate.get("worker_executor_pool"),
                    ),
                )
                return None, f"worker_admission:{reason}"

            cur = self._conn.execute(
                """
                WITH v AS (
                    SELECT id, status AS prev_status, enqueued_at
                    FROM jobs
                    WHERE id = ?
                      AND status IN ('QUEUED', 'FAILED_RETRYABLE')
                      AND attempt < max_attempts
                      AND (depends_on_job_id IS NULL OR EXISTS (
                        SELECT 1 FROM jobs d
                        WHERE d.id = jobs.depends_on_job_id AND d.status = 'SUCCEEDED'
                      ))
                )
                UPDATE jobs
                SET status = 'CLAIMED',
                    worker_id = ?,
                    attempt = attempt + 1,
                    claimed_at = ?,
                    queue_wait_ms = MAX(0, (? - COALESCE(jobs.enqueued_at, ?)) * 1000),
                    updated_at = ?
                FROM v
                WHERE jobs.id = v.id
                  AND jobs.status = v.prev_status
                RETURNING *
                """,
                (job_id, worker_id, now, now, now, now),
            )
            row = cur.fetchone()
            if row is None:
                self._conn.rollback()
                return None, "not_runnable"
            claimed_d: dict[str, Any] = dict(row)
            prev_status = str(probe_d.get("status") or "")
            append_job_event(
                self._conn,
                job_id=job_id,
                from_status=prev_status or None,
                to_status="CLAIMED",
                message="job claimed by run_job (from QUEUED or FAILED_RETRYABLE)",
                payload={"worker_id": worker_id},
            )
            self._conn.commit()
            logger.info(
                "job claimed",
                extra=make_log_extra(
                    trace_id=claimed_d.get("trace_id"),
                    job_id=claimed_d.get("id"),
                    job_type=claimed_d.get("job_type"),
                    session_id=claimed_d.get("session_id"),
                    photo_id=claimed_d.get("photo_id"),
                    worker_id=worker_id,
                    status="CLAIMED",
                ),
            )
            return claimed_d, None
        except Exception:
            self._conn.rollback()
            raise

    def update_status(
        self,
        job_id: int,
        *,
        to_status: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        from utils.luma_brain import update_job_status

        update_job_status(
            self._conn,
            job_id=job_id,
            to_status=to_status,
            message=message,
            payload=payload,
        )

    def start_pipeline_preprocessing(
        self,
        job_id: int,
        *,
        worker_id: int,
        session_id: Any,
        trace_id: str,
        job_type: str,
    ) -> None:
        self.update_status(
            job_id,
            to_status="PREPROCESSING",
            message="job preprocessing",
            payload={
                "worker_id": worker_id,
                "session_id": session_id,
                "trace_id": trace_id,
                "job_type": job_type,
            },
        )

    def start_pipeline_inferencing(
        self,
        job_id: int,
        *,
        source_dir: str | None,
        trace_id: str,
        job_type: str,
    ) -> None:
        self.update_status(
            job_id,
            to_status="INFERENCING",
            message="running aesthetic pipeline",
            payload={"source_dir": source_dir, "trace_id": trace_id, "job_type": job_type},
        )

    def succeed(self, job_id: int, **kwargs: Any) -> None:
        from utils.luma_brain import mark_job_succeeded

        mark_job_succeeded(self._conn, job_id=job_id, **kwargs)

    def fail_permanent(
        self,
        job_id: int,
        *,
        error_code: str,
        error_message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        from utils.luma_brain import fail_job_permanent

        fail_job_permanent(
            self._conn,
            job_id=job_id,
            error_code=error_code,
            error_message=error_message,
            payload=payload,
        )

    def fail_retryable(
        self,
        job_id: int,
        *,
        error_code: str,
        error_message: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        """Returns ``FAILED_RETRYABLE`` or ``DEAD_LETTERED`` (when attempts are exhausted)."""
        from utils.luma_brain import fail_job_retryable

        return fail_job_retryable(
            self._conn,
            job_id=job_id,
            error_code=error_code,
            error_message=error_message,
            payload=payload,
        )
