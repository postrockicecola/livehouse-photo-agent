"""
Systematic reliability / chaos-style scenarios for SSOT jobs, workers, dispatch, and inference.

Used by ``scripts/chaos_runtime.py`` and ``tests/test_reliability_chaos.py``. Each scenario
returns structured evidence for demos and interviews (not production chaos engineering).
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from inference.client import InferenceClient
from inference.parsers import clean_json_response, parse_dimensional_response
from inference.providers.base import InferenceProvider
from inference.providers.mock import MockProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse, inference_status_ok
from services.job_errors import classify_exception
from services.scheduler import DispatchPolicy, plan_dispatch
from utils.luma_brain import (
    ClaimFenceError,
    brain_connect,
    claim_jobs,
    create_job,
    fail_job_retryable,
    get_job,
    mark_job_succeeded,
    register_or_update_worker,
    requeue_stuck_jobs,
    set_worker_control_status,
)


@dataclass
class ChaosScenarioResult:
    """One scenario outcome: pass/fail plus narrative hooks for RELIABILITY.md / interviews."""

    id: str
    ok: bool
    design: str
    evidence: dict[str, Any] = field(default_factory=dict)
    interview_line: str = ""
    assertions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@contextmanager
def isolated_brain_db() -> Iterator[None]:
    """Point ``LUMA_BRAIN_DB`` at a fresh file for the duration of the block."""
    prev = os.environ.get("LUMA_BRAIN_DB")
    fd, path = tempfile.mkstemp(suffix="_chaos_luma_brain.db")
    os.close(fd)
    os.environ["LUMA_BRAIN_DB"] = path
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("LUMA_BRAIN_DB", None)
        else:
            os.environ["LUMA_BRAIN_DB"] = prev
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass


def scenario_dead_letter_after_retries() -> ChaosScenarioResult:
    """
    Repeated claim + fail_job_retryable until attempt >= max_attempts -> DEAD_LETTERED.
    """
    sid = "dead-letter-retries"
    design = (
        "SSOT job lifecycle: each claim increments ``attempt``; ``fail_job_retryable`` promotes "
        "to ``DEAD_LETTERED`` when retries are exhausted (bounded failure, no infinite retry)."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            wid = register_or_update_worker(
                conn,
                worker_name=f"chaos-{sid}-{int(time.time())}",
                worker_type="general",
                status="ONLINE",
                capacity=2,
            )
            jid = create_job(
                conn,
                job_type="CHAOS_BENCH",
                priority=0,
                max_attempts=3,
                trace_id=f"{sid}-{int(time.time())}",
            )
            statuses: list[str] = []
            for _ in range(3):
                claimed = claim_jobs(conn, worker_id=wid, job_type="CHAOS_BENCH", limit=1)
                if not claimed:
                    return ChaosScenarioResult(
                        id=sid,
                        ok=False,
                        design=design,
                        evidence={"error": "expected claim each round"},
                        interview_line="Dead-letter path did not get three claims — check runnable statuses.",
                        assertions=["claim_jobs returns a row each retry round"],
                        metrics={"claims_completed": len(statuses)},
                    )
                st = fail_job_retryable(conn, job_id=jid, error_message="simulated failure")
                statuses.append(st)
            row = get_job(conn, job_id=jid)
            final = str(row["status"]) if row else None
            ok = final == "DEAD_LETTERED" and statuses[-1] == "DEAD_LETTERED"
            return ChaosScenarioResult(
                id=sid,
                ok=ok,
                design=design,
                evidence={
                    "job_id": jid,
                    "fail_statuses_after_each_retry": statuses,
                    "final_job_status": final,
                    "final_attempt": int(row["attempt"]) if row else None,
                },
                interview_line=(
                    "After three failed attempts, the job lands in DEAD_LETTERED so the queue "
                    "does not spin forever — ops can manual_retry or inspect payload."
                ),
                assertions=[
                    "After max_attempts failures, job status is DEAD_LETTERED",
                    "Last fail_job_retryable status is DEAD_LETTERED",
                ],
                metrics={
                    "max_attempts_configured": 3,
                    "retry_rounds": len(statuses),
                    "final_attempt": int(row["attempt"]) if row else None,
                },
            )
        finally:
            conn.close()


def scenario_worker_pause_and_drain_block_new_claims() -> ChaosScenarioResult:
    """
    PAUSED / DRAINING workers do not pass worker_runtime_admission -> claim_jobs returns [].
    """
    sid = "worker-pause-drain"
    design = (
        "Control plane: ``PAUSED`` / ``DRAINING`` block **new** claims while preserving SSOT; "
        "only ``ONLINE`` workers with headroom may claim (see ``worker_runtime_admission``)."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            wid = register_or_update_worker(
                conn,
                worker_name=f"chaos-{sid}-{int(time.time())}",
                worker_type="general",
                status="ONLINE",
                capacity=2,
            )
            create_job(conn, job_type="CHAOS_BENCH", max_attempts=3, trace_id=f"{sid}-a")
            paused = set_worker_control_status(conn, worker_id=wid, to_status="PAUSED")
            c_paused = claim_jobs(conn, worker_id=wid, job_type="CHAOS_BENCH", limit=1)
            set_worker_control_status(conn, worker_id=wid, to_status="ONLINE")
            create_job(conn, job_type="CHAOS_BENCH", max_attempts=3, trace_id=f"{sid}-b")
            set_worker_control_status(conn, worker_id=wid, to_status="DRAINING")
            c_drain = claim_jobs(conn, worker_id=wid, job_type="CHAOS_BENCH", limit=1)
            ok = (
                paused.get("ok")
                and len(c_paused) == 0
                and len(c_drain) == 0
            )
            return ChaosScenarioResult(
                id=sid,
                ok=bool(ok),
                design=design,
                evidence={
                    "paused_control": paused,
                    "claims_while_paused": len(c_paused),
                    "claims_while_draining": len(c_drain),
                },
                interview_line=(
                    "Pause and drain are first-class worker states: the orchestrator stops handing "
                    "out new work without deleting rows, matching k8s-style drain semantics."
                ),
                assertions=[
                    "set_worker_control_status(PAUSED) succeeds",
                    "claim_jobs returns empty while PAUSED",
                    "claim_jobs returns empty while DRAINING",
                ],
                metrics={
                    "claims_while_paused": len(c_paused),
                    "claims_while_draining": len(c_drain),
                },
            )
        finally:
            conn.close()


def scenario_stale_worker_heartbeat_requeues_job() -> ChaosScenarioResult:
    """Stale ``workers.last_heartbeat`` + old ``claimed_at`` -> ``requeue_stuck_jobs`` -> QUEUED."""
    sid = "stale-heartbeat-requeue"
    design = (
        "Liveness: stuck active jobs are re-queued when the claiming worker's heartbeat is stale, "
        "so a crashed worker does not permanently pin work in CLAIMED."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            wid = register_or_update_worker(
                conn,
                worker_name=f"chaos-{sid}-{int(time.time())}",
                worker_type="general",
                status="ONLINE",
                capacity=1,
            )
            jid = create_job(conn, job_type="CHAOS_BENCH", max_attempts=3, trace_id=f"{sid}")
            claimed = claim_jobs(conn, worker_id=wid, job_type="CHAOS_BENCH", limit=1)
            if not claimed:
                return ChaosScenarioResult(
                    id=sid,
                    ok=False,
                    design=design,
                    evidence={"error": "claim failed"},
                    assertions=["Initial claim succeeds so job can be stuck in CLAIMED"],
                    metrics={},
                )
            now = int(time.time())
            conn.execute(
                "UPDATE workers SET last_heartbeat = ? WHERE id = ?",
                (now - 120, wid),
            )
            conn.execute(
                "UPDATE jobs SET claimed_at = ? WHERE id = ?",
                (now - 300, jid),
            )
            conn.commit()
            requeued = requeue_stuck_jobs(
                conn,
                stale_after_seconds=60,
                worker_stale_after_seconds=30,
                limit=20,
                reason="chaos: stale heartbeat",
            )
            row = get_job(conn, job_id=jid)
            final = str(row["status"]) if row else None
            ok = jid in requeued and final == "QUEUED"
            return ChaosScenarioResult(
                id=sid,
                ok=ok,
                design=design,
                evidence={
                    "job_id": jid,
                    "requeued_job_ids": requeued,
                    "job_status_after": final,
                },
                interview_line=(
                    "We couple job staleness with worker heartbeat so recovery is safe: "
                    "only when both look abandoned do we put work back on the queue."
                ),
                assertions=[
                    "requeue_stuck_jobs includes the stuck job id",
                    "Job returns to QUEUED after requeue",
                ],
                metrics={
                    "requeued_count": len(requeued),
                    "stale_after_seconds": 60,
                    "worker_stale_after_seconds": 30,
                },
            )
        finally:
            conn.close()


def scenario_claim_fence_blocks_zombie_succeed() -> ChaosScenarioResult:
    """After stuck-requeue bumps ``claim_generation``, old claim cannot mark SUCCEEDED."""
    sid = "claim-fence-zombie"
    design = (
        "Fencing: ``claim_generation`` increments on claim and stuck-requeue. Terminal writers "
        "pass the generation they claimed; a zombie after requeue raises ClaimFenceError."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            wid = register_or_update_worker(
                conn,
                worker_name=f"chaos-{sid}-{int(time.time())}",
                worker_type="general",
                status="ONLINE",
                capacity=1,
            )
            jid = create_job(conn, job_type="CHAOS_BENCH", max_attempts=3, trace_id=f"{sid}")
            claimed = claim_jobs(conn, worker_id=wid, job_type="CHAOS_BENCH", limit=1)
            if not claimed:
                return ChaosScenarioResult(
                    id=sid,
                    ok=False,
                    design=design,
                    evidence={"error": "claim failed"},
                    assertions=["Initial claim succeeds"],
                    metrics={},
                )
            gen_at_claim = int(claimed[0].get("claim_generation") or 0)
            now = int(time.time())
            conn.execute(
                "UPDATE workers SET last_heartbeat = ? WHERE id = ?",
                (now - 120, wid),
            )
            conn.execute(
                "UPDATE jobs SET claimed_at = ? WHERE id = ?",
                (now - 300, jid),
            )
            conn.commit()
            requeued = requeue_stuck_jobs(
                conn,
                stale_after_seconds=60,
                worker_stale_after_seconds=30,
                limit=20,
                reason="chaos: fence requeue",
            )
            row = get_job(conn, job_id=jid)
            gen_after = int((row or {}).get("claim_generation") or 0)
            fenced = False
            fence_msg = ""
            try:
                mark_job_succeeded(
                    conn,
                    job_id=jid,
                    fence_claim_generation=gen_at_claim,
                    fence_worker_id=wid,
                )
            except ClaimFenceError as exc:
                fenced = True
                fence_msg = str(exc)
            final = get_job(conn, job_id=jid)
            final_status = str((final or {}).get("status") or "")
            ok = (
                jid in requeued
                and gen_after > gen_at_claim
                and fenced
                and final_status == "QUEUED"
            )
            return ChaosScenarioResult(
                id=sid,
                ok=ok,
                design=design,
                evidence={
                    "job_id": jid,
                    "claim_generation_at_claim": gen_at_claim,
                    "claim_generation_after_requeue": gen_after,
                    "zombie_succeed_fenced": fenced,
                    "fence_message": fence_msg,
                    "job_status_after": final_status,
                },
                interview_line=(
                    "Stuck-requeue is not just status flip: claim_generation fences out the "
                    "abandoned writer so a late SUCCEEDED cannot clobber a reclaimed job."
                ),
                assertions=[
                    "requeue bumps claim_generation",
                    "mark_job_succeeded with stale generation raises ClaimFenceError",
                    "job remains QUEUED after fenced zombie succeed",
                ],
                metrics={
                    "claim_generation_at_claim": gen_at_claim,
                    "claim_generation_after_requeue": gen_after,
                },
            )
        finally:
            conn.close()


class _AlwaysErrorPrimary(InferenceProvider):
    PROVIDER_ID = "chaos_primary_err"

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        return InferenceResponse(
            status="error",
            error="chaos primary always fails",
            model=model_name,
            metadata={"provider": "primary"},
        )


def scenario_inference_fallback_provider() -> ChaosScenarioResult:
    """Primary errors; router uses fallback; client surfaces degraded / successful path."""
    sid = "inference-fallback"
    design = (
        "Inference router: primary failure triggers fallback provider so user-facing latency "
        "is bounded when the secondary model is healthy."
    )
    router = InferenceRouter(
        primary_provider=_AlwaysErrorPrimary(),
        primary_model_name="bad",
        fallback_provider=MockProvider(fixed_text='{"score": 6.0}', model_name="mock-fb"),
        fallback_model_name="mock-fb",
    )
    client = InferenceClient(router=router, num_workers=1, max_retries=0, timeout=5)
    out = client.predict("/tmp/chaos-fallback.jpg", '{"task":"score"}', trace_id=sid)
    meta = dict(out.get("metadata") or {})
    ok = bool(meta.get("degraded")) and inference_status_ok(str(out.get("status") or ""))
    return ChaosScenarioResult(
        id=sid,
        ok=ok,
        design=design,
        evidence={
            "status": out.get("status"),
            "degraded": meta.get("degraded"),
            "text_preview": (str(out.get("text") or ""))[:80],
        },
        interview_line=(
            "Fallback is explicit in metadata (degraded), so SLO dashboards can separate "
            "healthy primary traffic from emergency secondary completions."
        ),
        assertions=[
            "Inference response status is success or degraded after router fallback",
            "Client metadata marks degraded when secondary path used",
        ],
        metrics={
            "client_max_retries": 0,
            "timeout_seconds": 5,
            "text_preview_len": len(str(out.get("text") or "")),
        },
    )


def scenario_malformed_model_json_parse_safe() -> ChaosScenarioResult:
    """Truncated JSON from provider does not crash; parser returns empty-ish structure."""
    sid = "malformed-json"
    design = (
        "Defensive parsing: ``clean_json_response`` + ``parse_dimensional_response`` swallow "
        "bad model text without taking down the worker process."
    )
    bad = '{"score": 8, "comments": {"composition": "ok"'  # truncated
    cleaned = clean_json_response(bad)
    parsed = parse_dimensional_response(cleaned, raw_model_text=bad)
    # Hard failure returns ``{}`` after ``_fallback_parse_truncated_json`` — still no uncaught exception.
    ok = isinstance(parsed, dict)
    return ChaosScenarioResult(
        id=sid,
        ok=ok,
        design=design,
        evidence={
            "cleaned_len": len(cleaned),
            "parsed_is_empty": parsed == {},
            "dimensions_keys_sample": list((parsed.get("dimensions") or {}).keys())[:4],
        },
        interview_line=(
            "Bad JSON is a fact of life with VLMs; we fail closed into a safe dict and log, "
            "instead of an uncaught parse exception."
        ),
        assertions=[
            "Truncated JSON does not raise uncaught exception",
            "parse_dimensional_response returns a dict",
        ],
        metrics={
            "cleaned_json_chars": len(cleaned),
            "parsed_empty": parsed == {},
        },
    )


def scenario_missing_source_dir_permanent_class() -> ChaosScenarioResult:
    """Missing ANALYZE_PATH directory is classified permanent (bad input), not transient."""
    sid = "missing-source-dir"
    design = (
        "Job executor error taxonomy: ``FileNotFoundError`` for missing ``source_dir`` maps to "
        "permanent failure so retries do not hammer NFS/object store."
    )
    exc = FileNotFoundError(
        "ANALYZE_PATH job 1: source_dir is not a directory or is inaccessible"
    )
    bucket = classify_exception(exc)
    ok = bucket == "permanent"
    return ChaosScenarioResult(
        id=sid,
        ok=ok,
        design=design,
        evidence={"exception_type": type(exc).__name__, "classify_bucket": bucket},
        interview_line=(
            "We separate retryable infra errors from permanent misconfiguration; "
            "missing paths go straight to FAILED_PERMANENT-style handling in the executor."
        ),
        assertions=[
            "Missing ANALYZE_PATH source_dir maps to permanent (not retryable)",
        ],
        metrics={"classify_bucket": bucket},
    )


def _seed_online_worker(conn: Any, *, name: str, capacity: int = 10, inflight: int = 0) -> int:
    wid = register_or_update_worker(
        conn,
        worker_name=name,
        worker_type="general",
        status="ONLINE",
        capacity=capacity,
    )
    conn.execute(
        "UPDATE workers SET inflight = ? WHERE id = ?",
        (inflight, wid),
    )
    conn.commit()
    return wid


def scenario_dispatch_weighted_fairness() -> ChaosScenarioResult:
    """
    ``select_jobs_weighted_fair`` interleaves job types; ANALYZE_PATH precedes ANALYZE_SESSION in RR.
    """
    sid = "dispatch-fairness"
    design = (
        "Scheduler policy: weighted round-robin across ``job_type`` caps prevents one type from "
        "starving others; ordering follows ``services.scheduler._TYPE_DISPATCH_ORDER``."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            _seed_online_worker(conn, name=f"chaos-dispatch-{int(time.time())}", capacity=20)
            now = int(time.time())
            candidates: list[dict[str, Any]] = []
            job_types = [
                "ANALYZE_SESSION",
                "ANALYZE_SESSION",
                "ANALYZE_PATH",
                "ANALYZE_PATH",
                "PIPELINE_STAGE",
            ]
            for i, jt in enumerate(job_types):
                candidates.append(
                    {
                        "id": 100 + i,
                        "job_type": jt,
                        "priority": 0,
                        "enqueued_at": now + i,
                    }
                )
            policy = DispatchPolicy(
                max_per_round=8,
                respect_headroom=False,
                per_type_max={"ANALYZE_SESSION": 10, "ANALYZE_PATH": 10, "PIPELINE_STAGE": 10},
            )
            plan = plan_dispatch(conn, candidates, policy=policy)
            ids = plan.selected_job_ids
            id_to_type = {100 + i: job_types[i] for i in range(len(job_types))}
            types_order = [id_to_type[int(x)] for x in ids]
            ok = len(ids) == 5 and types_order[0] == "ANALYZE_PATH"
            return ChaosScenarioResult(
                id=sid,
                ok=ok,
                design=design,
                evidence={
                    "selected_job_ids": ids,
                    "types_in_order": types_order,
                    "by_type_chosen": plan.by_type_chosen,
                },
                interview_line=(
                    "Dispatch is not pure FIFO across heterogeneous job types: we cap per type "
                    "and round-robin so batch ingest cannot drown interactive path jobs."
                ),
                assertions=[
                    "plan_dispatch selects all five candidate jobs",
                    "First selected type is ANALYZE_PATH (weighted fairness / ordering)",
                ],
                metrics={
                    "candidates_count": len(candidates),
                    "selected_count": len(ids),
                    "by_type_chosen": plan.by_type_chosen,
                },
            )
        finally:
            conn.close()


def scenario_dispatch_headroom_zero_when_all_paused() -> ChaosScenarioResult:
    """When workers exist but none are ONLINE, effective_max is 0 (no blast past capacity)."""
    sid = "dispatch-headroom-paused"
    design = (
        "Cluster headroom: if every registered worker is non-ONLINE, ``plan_dispatch`` yields "
        "``effective_max=0`` — Celery should not flood run_job when no one can admit work."
    )
    with isolated_brain_db():
        conn = brain_connect()
        try:
            wid = _seed_online_worker(conn, name=f"chaos-hr-{int(time.time())}", capacity=5)
            set_worker_control_status(conn, worker_id=wid, to_status="PAUSED")
            now = int(time.time())
            candidates = [
                {"id": 1, "job_type": "ANALYZE_SESSION", "priority": 1, "enqueued_at": now},
            ]
            policy = DispatchPolicy(max_per_round=32, respect_headroom=True)
            plan = plan_dispatch(conn, candidates, policy=policy)
            ok = (
                plan.effective_max == 0
                and len(plan.selected_job_ids) == 0
                and plan.note == "no_online_workers_no_dispatch"
            )
            return ChaosScenarioResult(
                id=sid,
                ok=ok,
                design=design,
                evidence={
                    "effective_max": plan.effective_max,
                    "note": plan.note,
                    "total_worker_rows": plan.total_worker_rows,
                    "online_workers": plan.online_workers,
                },
                interview_line=(
                    "Headroom-aware dispatch connects the SQLite worker registry to scheduling: "
                    "all-paused is equivalent to zero capacity for new dispatches."
                ),
                assertions=[
                    "effective_max is 0 when no worker can admit work (all paused)",
                    "plan_dispatch selects no jobs",
                    'note is "no_online_workers_no_dispatch"',
                ],
                metrics={
                    "effective_max": plan.effective_max,
                    "online_workers": plan.online_workers,
                    "total_worker_rows": plan.total_worker_rows,
                },
            )
        finally:
            conn.close()


ALL_SCENARIOS: tuple[Callable[[], ChaosScenarioResult], ...] = (
    scenario_dead_letter_after_retries,
    scenario_worker_pause_and_drain_block_new_claims,
    scenario_stale_worker_heartbeat_requeues_job,
    scenario_claim_fence_blocks_zombie_succeed,
    scenario_inference_fallback_provider,
    scenario_malformed_model_json_parse_safe,
    scenario_missing_source_dir_permanent_class,
    scenario_dispatch_weighted_fairness,
    scenario_dispatch_headroom_zero_when_all_paused,
)

SCENARIO_BY_ID: dict[str, Callable[[], ChaosScenarioResult]] = {
    "dead-letter-retries": scenario_dead_letter_after_retries,
    "worker-pause-drain": scenario_worker_pause_and_drain_block_new_claims,
    "stale-heartbeat-requeue": scenario_stale_worker_heartbeat_requeues_job,
    "claim-fence-zombie": scenario_claim_fence_blocks_zombie_succeed,
    "inference-fallback": scenario_inference_fallback_provider,
    "malformed-json": scenario_malformed_model_json_parse_safe,
    "missing-source-dir": scenario_missing_source_dir_permanent_class,
    "dispatch-fairness": scenario_dispatch_weighted_fairness,
    "dispatch-headroom-paused": scenario_dispatch_headroom_zero_when_all_paused,
}


def run_all_scenarios() -> list[ChaosScenarioResult]:
    return [fn() for fn in ALL_SCENARIOS]


def run_scenarios(*, only_ids: frozenset[str] | None = None) -> list[ChaosScenarioResult]:
    if not only_ids:
        return run_all_scenarios()
    out: list[ChaosScenarioResult] = []
    for sid in sorted(only_ids):
        fn = SCENARIO_BY_ID.get(sid)
        if fn is None:
            out.append(
                ChaosScenarioResult(
                    id=sid,
                    ok=False,
                    design="unknown scenario id",
                    evidence={"known": sorted(SCENARIO_BY_ID.keys())},
                    assertions=["scenario id exists in SCENARIO_BY_ID registry"],
                    metrics={"unknown_id": sid},
                )
            )
            continue
        out.append(fn())
    return out


def results_to_jsonable(results: list[ChaosScenarioResult]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in results:
        out.append(
            {
                "id": r.id,
                "ok": r.ok,
                "design": r.design,
                "interview_line": r.interview_line,
                "assertions": r.assertions,
                "metrics": r.metrics,
                "evidence": r.evidence,
            }
        )
    return out


def print_report(results: list[ChaosScenarioResult], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(results_to_jsonable(results), ensure_ascii=False, indent=2))
        return
    passed = sum(1 for r in results if r.ok)
    print("# Chaos / runtime reliability matrix")
    print(f"passed {passed}/{len(results)}")
    print("")
    for r in results:
        mark = "PASS" if r.ok else "FAIL"
        print(f"## [{mark}] {r.id}")
        print(f"design: {r.design}")
        print(f"interview: {r.interview_line}")
        if r.assertions:
            print(f"assertions: {json.dumps(r.assertions, ensure_ascii=False)}")
        if r.metrics:
            print(f"metrics: {json.dumps(r.metrics, ensure_ascii=False)}")
        print(f"evidence: {json.dumps(r.evidence, ensure_ascii=False)}")
        print("")
