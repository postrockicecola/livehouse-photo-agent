#!/usr/bin/env python3
"""Fault injection helpers for provider failures and stuck-job recovery."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from inference.client import InferenceClient
from inference.parsers import clean_json_response, parse_dimensional_response
from inference.providers.base import InferenceProvider
from inference.providers.mock import MockProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse
from utils.luma_brain import (
    brain_connect,
    claim_jobs,
    create_job,
    register_or_update_worker,
    requeue_stuck_jobs,
)


class TimeoutProvider(InferenceProvider):
    PROVIDER_ID = "sim_timeout"

    def __init__(self, sleep_seconds: float = 1.5) -> None:
        self.sleep_seconds = max(0.1, sleep_seconds)

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        time.sleep(self.sleep_seconds)
        return InferenceResponse(status="error", error="simulated timeout", model=model_name)


class MalformedProvider(InferenceProvider):
    PROVIDER_ID = "sim_malformed"

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        return InferenceResponse(
            status="success",
            text='{"score": 8, "comments": {"composition": "ok"',  # missing closing braces
            model=model_name,
            metadata={"malformed": True},
        )


def simulate_provider_timeout() -> None:
    router = InferenceRouter(
        primary_provider=TimeoutProvider(sleep_seconds=1.2),
        primary_model_name="timeout-primary",
        fallback_provider=MockProvider(fixed_text='{"score": 6.2}', model_name="mock-fallback"),
        fallback_model_name="mock-fallback",
    )
    client = InferenceClient(router=router, queue_wait_timeout_seconds=0.1, num_workers=1, max_retries=0, timeout=2)
    out = client.predict("/tmp/timeout.jpg", '{"task":"timeout-test"}')
    print("# provider-timeout")
    print(json.dumps(out, ensure_ascii=False, indent=2))


def simulate_malformed_response() -> None:
    router = InferenceRouter(
        primary_provider=MalformedProvider(),
        primary_model_name="bad-json-primary",
        fallback_provider=MockProvider(fixed_text='{"score": 5.5}', model_name="mock-fallback"),
        fallback_model_name="mock-fallback",
    )
    client = InferenceClient(router=router, num_workers=1, max_retries=0, timeout=2)
    out = client.predict("/tmp/malformed.jpg", '{"task":"json-parse-test"}')
    cleaned = clean_json_response(str(out.get("text") or ""))
    parsed = parse_dimensional_response(cleaned, raw_model_text=str(out.get("text") or ""))
    print("# malformed-response")
    print(json.dumps({"raw": out, "cleaned": cleaned, "parsed": parsed}, ensure_ascii=False, indent=2))


def simulate_stuck_job_recovery(stale_after_seconds: int = 2, worker_stale_seconds: int = 1) -> None:
    conn = brain_connect()
    try:
        worker_id = register_or_update_worker(
            conn,
            worker_name=f"sim-worker-{int(time.time())}",
            worker_type="general",
            status="ONLINE",
            capacity=1,
        )
        job_id = create_job(conn, job_type="BENCH_SIM", priority=10, max_attempts=3, trace_id=f"sim-{int(time.time())}")
        claimed = claim_jobs(conn, worker_id=worker_id, job_type="BENCH_SIM", limit=1)
        if not claimed:
            raise RuntimeError("failed to claim simulated job")
        conn.execute(
            "UPDATE workers SET last_heartbeat = ? WHERE id = ?",
            (int(time.time()) - max(2, worker_stale_seconds + 1), worker_id),
        )
        conn.execute(
            "UPDATE jobs SET claimed_at = ? WHERE id = ?",
            (int(time.time()) - max(3, stale_after_seconds + 1), job_id),
        )
        conn.commit()
        requeued = requeue_stuck_jobs(
            conn,
            stale_after_seconds=stale_after_seconds,
            worker_stale_after_seconds=worker_stale_seconds,
            limit=20,
            reason="simulated worker crash/stuck job",
        )
        refreshed = conn.execute("SELECT id, status, worker_id, claimed_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        row_dict = dict(refreshed) if refreshed else None
        recovery_ok = bool(row_dict and str(row_dict.get("status")) == "QUEUED" and job_id in requeued)
        print("# worker-crash-stuck-job")
        print(
            json.dumps(
                {
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "requeued_jobs": requeued,
                    "job_after_requeue": row_dict,
                    "recovery_ok": recovery_ok,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run simple fault-injection scenarios.")
    parser.add_argument(
        "--scenario",
        choices=["timeout", "malformed", "stuck-job", "chaos-matrix", "all"],
        default="all",
        help=(
            "Failure scenario to run. Use chaos-matrix for the full SSOT+dispatch matrix "
            "(isolated DB per sub-scenario); see docs/RELIABILITY.md."
        ),
    )
    args = parser.parse_args()

    if args.scenario in {"timeout", "all"}:
        simulate_provider_timeout()
    if args.scenario in {"malformed", "all"}:
        simulate_malformed_response()
    if args.scenario in {"stuck-job", "all"}:
        simulate_stuck_job_recovery()
    if args.scenario in {"chaos-matrix", "all"}:
        from reliability_scenarios import print_report, run_all_scenarios

        chaos_results = run_all_scenarios()
        print_report(chaos_results, as_json=False)
        if not all(r.ok for r in chaos_results):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
