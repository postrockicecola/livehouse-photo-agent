"""
Stage-3 (VLM) inference admission: reuse :func:`services.scheduler.plan_dispatch` so pipeline
inference respects cluster headroom, per-provider caps, and fairness like Celery job dispatch.

Candidate rows are synthetic (``id`` = 1..N over sorted filenames) and are never written to ``jobs``.
"""
from __future__ import annotations

import time
from typing import Any

from services.scheduler import DispatchPlan, DispatchPolicy, plan_dispatch


def _job_scope_for_pipeline(conn: Any, job_id: int) -> tuple[str | None, str | None]:
    row = conn.execute(
        "SELECT namespace, project_key FROM jobs WHERE id = ?",
        (int(job_id),),
    ).fetchone()
    if row is None:
        return None, None
    return row["namespace"], row["project_key"]


def build_stage3_dispatch_candidates(
    incoming: list[dict[str, Any]],
    *,
    job_id: int,
    provider: str,
    conn: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Pair each eligibility row (Stage 2 pass) with a dispatch candidate row.

    Returns ``(candidates, incoming_sorted)`` where candidate ``id`` ∈ 1..N matches
    position in ``incoming_sorted`` (1-based). Candidate ``priority`` is derived from
    ``fast_score`` so higher-scoring images are preferred when the dispatch planner
    admits a subset under caps.
    """
    incoming_sorted = sorted(incoming, key=lambda x: str(x["file_name"]))
    ns, pk = _job_scope_for_pipeline(conn, job_id)
    now = int(time.time())
    candidates: list[dict[str, Any]] = []
    for i, row in enumerate(incoming_sorted, start=1):
        try:
            fs = float(row.get("fast_score") or 0.0)
        except (TypeError, ValueError):
            fs = 0.0
        # Higher Stage2 score → higher dispatch priority within PIPELINE_STAGE (fair selection).
        priority_int = max(0, min(2**31 - 1, int(round(fs * 1_000_000.0))))
        candidates.append(
            {
                "id": i,
                "job_type": "PIPELINE_STAGE",
                "priority": priority_int,
                "enqueued_at": now,
                "provider": provider,
                "stage_name": "STAGE3_VLM",
                "payload_json": None,
                "namespace": ns,
                "project_key": pk,
            }
        )
    return candidates, incoming_sorted


def plan_stage3_inference_dispatch(
    conn: Any,
    incoming: list[dict[str, Any]],
    *,
    job_id: int,
    provider: str,
    policy: DispatchPolicy | None = None,
) -> tuple[DispatchPlan, list[dict[str, Any]], set[int]]:
    """
    Run dispatch policy over Stage-3 candidates.

    Returns ``(plan, incoming_sorted, selected_ids_set)`` where ``selected_ids_set`` contains
    integer ids 1..N (same convention as :meth:`DispatchPlan.selected_job_ids`).
    """
    candidates, incoming_sorted = build_stage3_dispatch_candidates(
        incoming, job_id=job_id, provider=provider, conn=conn
    )
    # plan_dispatch may mutate rows (required_executor_class, etc.)
    plan = plan_dispatch(conn, [dict(c) for c in candidates], policy=policy)
    selected = {int(x) for x in plan.selected_job_ids}
    return plan, incoming_sorted, selected
