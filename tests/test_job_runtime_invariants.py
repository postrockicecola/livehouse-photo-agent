from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from harness.reliability_scenarios import isolated_brain_db
from utils.luma_brain import (
    brain_connect,
    claim_jobs,
    create_job,
    get_job,
    register_or_update_worker,
    requeue_stuck_jobs,
    seed_analyze_session_jobs,
    update_job_status,
)


def test_stale_exhausted_claim_is_dead_lettered_not_unclaimable_queued() -> None:
    with isolated_brain_db():
        conn = brain_connect()
        try:
            worker_id = register_or_update_worker(
                conn,
                worker_name="exhaustion-test",
                worker_type="general",
                status="ONLINE",
                capacity=1,
            )
            job_id = create_job(conn, job_type="TEST", max_attempts=1)
            claimed = claim_jobs(conn, worker_id=worker_id, job_type="TEST", limit=1)
            assert [row["id"] for row in claimed] == [job_id]

            now = int(time.time())
            conn.execute(
                "UPDATE workers SET last_heartbeat=? WHERE id=?",
                (now - 120, worker_id),
            )
            conn.execute(
                "UPDATE jobs SET claimed_at=? WHERE id=?",
                (now - 120, job_id),
            )
            conn.commit()

            requeued = requeue_stuck_jobs(
                conn,
                stale_after_seconds=60,
                worker_stale_after_seconds=60,
            )
            assert requeued == []
            assert get_job(conn, job_id=job_id)["status"] == "DEAD_LETTERED"
        finally:
            conn.close()


def test_concurrent_analyze_seed_creates_one_runnable_job() -> None:
    with isolated_brain_db():
        conn = brain_connect()
        try:
            now = int(time.time())
            session_id = int(
                conn.execute(
                    """
                    INSERT INTO sessions(
                        session_key, session_dir, archive_root, started_at
                    ) VALUES ('s1', '/archive/s1', '/archive', ?)
                    """,
                    (now,),
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO photos(
                    file_hash, file_path, status, session_id, created_at
                ) VALUES ('hash-1', '/archive/s1/RAW/a.ARW', 'INGESTED', ?, ?)
                """,
                (session_id, now),
            )
            conn.commit()
        finally:
            conn.close()

        def seed() -> int:
            worker_conn = brain_connect()
            try:
                return int(seed_analyze_session_jobs(worker_conn)["created_jobs"])
            finally:
                worker_conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            created = list(pool.map(lambda _: seed(), range(2)))

        conn = brain_connect()
        try:
            count = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM jobs
                    WHERE job_type='ANALYZE_SESSION' AND session_id=?
                      AND status IN (
                        'QUEUED','CLAIMED','PREPROCESSING','INFERENCING',
                        'POSTPROCESSING','FAILED_RETRYABLE'
                      )
                    """,
                    (session_id,),
                ).fetchone()["c"]
            )
        finally:
            conn.close()
        assert sum(created) == 1
        assert count == 1


def test_cancel_invalidates_active_claim_generation() -> None:
    with isolated_brain_db():
        conn = brain_connect()
        try:
            worker_id = register_or_update_worker(
                conn,
                worker_name="cancel-fence-test",
                worker_type="general",
                status="ONLINE",
                capacity=1,
            )
            job_id = create_job(conn, job_type="TEST")
            claimed = claim_jobs(conn, worker_id=worker_id, job_type="TEST", limit=1)[0]
            generation = int(claimed["claim_generation"])

            update_job_status(
                conn,
                job_id=job_id,
                to_status="CANCELLED",
                message="test cancel",
            )
            cancelled = get_job(conn, job_id=job_id)
            assert cancelled["status"] == "CANCELLED"
            assert int(cancelled["claim_generation"]) == generation + 1
            assert cancelled["worker_id"] is None
        finally:
            conn.close()
