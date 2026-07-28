"""Infra-oriented API routes for jobs/workers/providers/metrics.

These endpoints read/write the same SQLite ``jobs`` / ``workers`` SSOT as Celery executors
(``services/job_executor.py``). This module is **not** a parallel stack—only HTTP admin/ops on the main path.
"""
from __future__ import annotations

import json
import os
from collections import deque
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from utils.http_security import require_ops_auth

router = APIRouter()


class InfraJobActionResponse(BaseModel):
    ok: bool
    job_id: int
    status: str | None = None
    message: str | None = None


class InfraJobListResponse(BaseModel):
    count: int
    offset: int
    limit: int
    items: list[dict[str, Any]]


class InfraJobEvent(BaseModel):
    id: int
    job_id: int
    from_status: str | None = None
    to_status: str | None = None
    created_at: int
    message: str | None = None
    payload_json: str | None = None


class InfraProjectScope(BaseModel):
    """Resolved platform scope for the anchor job (``jobs.namespace`` / ``jobs.project_key``)."""

    namespace: str = Field(default="default", description="Logical partition within one deployment (default ``default``).")
    project_key: str = Field(default="default", description="Product or tenant label within the namespace (default ``default``).")


class InfraJobDetailResponse(BaseModel):
    job: dict[str, Any]
    events: list[InfraJobEvent]
    model_runs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    primary_artifact: dict[str, Any] | None = None
    project_scope: InfraProjectScope


class InfraTimelineTimeWindow(BaseModel):
    """Unix-seconds span covering displayed spans (rough wall-clock for the waterfall)."""

    t0: int
    t1: int
    width_seconds: int


class InfraTimelineSpan(BaseModel):
    id: str
    kind: str
    ts: int
    label: str
    from_status: str | None = None
    to_status: str | None = None
    duration_ms: int | None = None
    queue_wait_ms: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class InfraJobTimelineResponse(BaseModel):
    """Aggregated per-job view for a console timeline."""

    job: dict[str, Any]
    project_scope: InfraProjectScope
    trace_id: str | None = None
    related_job_ids: list[int] = Field(default_factory=list)
    events: list[dict[str, Any]]
    model_runs: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    primary_artifact: dict[str, Any] | None = None
    worker: dict[str, Any] | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    spans: list[InfraTimelineSpan] = Field(default_factory=list)
    time_window: InfraTimelineTimeWindow
    job_relationships: dict[str, Any] = Field(default_factory=dict)
    job_graph: dict[str, Any] = Field(default_factory=dict)
class InfraTraceLookupResponse(InfraJobTimelineResponse):
    """Same payload as a single-job timeline, plus trace-wide ids for linking."""

    anchor_job_id: int
    job_ids: list[int] = Field(default_factory=list)


class InfraWorkersResponse(BaseModel):
    count: int
    items: list[dict[str, Any]]
    broker: dict[str, Any] = Field(default_factory=dict)
    unmatched_broker_workers: list[dict[str, Any]] = Field(default_factory=list)


class InfraWorkerPoolsResponse(BaseModel):
    """Executor pool routing snapshot (logical isolation model; Celery broker unchanged)."""

    routing_executor_classes: list[str]
    known_executor_classes: list[str]
    executor_pool_headroom: dict[str, Any]
    workers_by_executor_pool: list[dict[str, Any]]


class InfraWorkerActionResponse(BaseModel):
    """Result of control-plane actions on a worker row (pause / resume / drain)."""

    ok: bool
    worker_id: int
    status: str | None = None
    from_status: str | None = None
    message: str | None = None


class InfraProviderItem(BaseModel):
    name: str
    enabled: bool
    endpoint: str | None = None
    model_name: str | None = None
    fallback_model_name: str | None = None
    runtime: dict[str, Any] | None = None
    display_name: str | None = None
    description: str | None = None
    supports_remote_endpoint: bool | None = None


class InfraProvidersResponse(BaseModel):
    active_provider: str
    providers: list[InfraProviderItem]


class InfraMetricsJobs(BaseModel):
    total: int
    runnable: int
    active: int
    by_status: dict[str, int] = Field(default_factory=dict)


class InfraMetricsWorkers(BaseModel):
    by_status: dict[str, int] = Field(default_factory=dict)


class InfraMetricsResponse(BaseModel):
    jobs: dict[str, Any]
    queue_backlog: dict[str, Any]
    workers: dict[str, Any]
    model_runs: dict[str, Any] = Field(default_factory=dict)
    providers: list[dict[str, Any]]
    inference_latency: dict[str, Any]
    latency: dict[str, Any] = Field(default_factory=dict)
    slo: dict[str, Any] = Field(default_factory=dict)
    inference_queue: dict[str, Any] = Field(default_factory=dict)
    metrics_authority: dict[str, Any] = Field(default_factory=dict)
    inference_from_database: dict[str, Any] = Field(default_factory=dict)
    runtime_snapshots: dict[str, Any] = Field(default_factory=dict)
    stage3_fast_first_routing: dict[str, Any] = Field(default_factory=dict)


class InfraMetricsHistoryResponse(BaseModel):
    """Server-persisted control-plane time-series so trends survive page reloads."""

    count: int
    window_sec: int
    points: list[dict[str, Any]]


class InfraBrainDashboardResponse(BaseModel):
    """SQLite ledger overview: table cardinalities, sessions, photos (ingest/outcome SSOT)."""

    db_path: str
    table_counts: dict[str, int] = Field(default_factory=dict)
    photos_by_status: dict[str, int] = Field(default_factory=dict)
    jobs_by_type: dict[str, int] = Field(default_factory=dict)
    sessions: list[dict[str, Any]] = Field(default_factory=list)
    photos: list[dict[str, Any]] = Field(default_factory=list)
    limits: dict[str, int] = Field(default_factory=dict)


class InfraRuntimeEventItem(BaseModel):
    id: int
    job_id: int
    from_status: str | None = None
    to_status: str | None = None
    created_at: int
    message: str | None = None
    stage_name: str | None = None
    worker_id: int | None = None
    worker_name: str | None = None
    trace_id: str | None = None


class InfraStageFlowItem(BaseModel):
    stage_key: str
    status: str
    count: int
    avg_latency_ms: int | None = None


class InfraRuntimeStreamResponse(BaseModel):
    """Recent orchestration events + per-stage job counts for the Brain control plane."""

    events: list[InfraRuntimeEventItem]
    stages: list[InfraStageFlowItem]
    retries_recent: list[InfraRuntimeEventItem] = Field(default_factory=list)


def _with_conn() -> Any:
    from utils.luma_brain import brain_connect

    return brain_connect()


def _project_scope_from_job(job: dict[str, Any]) -> dict[str, str]:
    from utils.luma_brain import _DEFAULT_JOB_NAMESPACE, _DEFAULT_PROJECT_KEY

    return {
        "namespace": str(job.get("namespace") or _DEFAULT_JOB_NAMESPACE),
        "project_key": str(job.get("project_key") or _DEFAULT_PROJECT_KEY),
    }


def _hydrate_model_runs_with_attempts(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from utils.luma_brain import list_model_run_attempts_for_runs

    if not rows:
        return rows
    rids = [int(r["id"]) for r in rows if r.get("id") is not None]
    by_run = list_model_run_attempts_for_runs(conn, run_ids=rids)
    return [{**dict(r), "attempts": list(by_run.get(int(r["id"]), []))} for r in rows]


_FAILED_STATUSES = frozenset(
    {"FAILED_PERMANENT", "FAILED_RETRYABLE", "DEAD_LETTERED", "CANCELLED"}
)


def _job_row_latency_ms(row: dict[str, Any]) -> int:
    v = row.get("total_latency_ms")
    if v is None:
        return 0
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def _timeline_node_summary(row: dict[str, Any]) -> dict[str, Any]:
    jid = int(row["id"])
    return {
        "job_id": jid,
        "job_type": row.get("job_type"),
        "stage_name": row.get("stage_name"),
        "stage_order": row.get("stage_order"),
        "status": row.get("status"),
        "total_latency_ms": row.get("total_latency_ms"),
        "root_job_id": int(row["root_job_id"]) if row.get("root_job_id") is not None else None,
        "parent_job_id": int(row["parent_job_id"]) if row.get("parent_job_id") is not None else None,
        "depends_on_job_id": int(row["depends_on_job_id"]) if row.get("depends_on_job_id") is not None else None,
        "is_terminal_failure": str(row.get("status") or "") in _FAILED_STATUSES,
    }


def _relationships_for_job(job: dict[str, Any], group_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Parent / child / root / depends for the **current** timeline job (same connection, in-memory group)."""
    jid = int(job["id"])
    root = job.get("root_job_id")
    root_i = int(root) if root is not None else jid
    parent = job.get("parent_job_id")
    dep = job.get("depends_on_job_id")
    children = [int(r["id"]) for r in group_rows if r.get("parent_job_id") is not None and int(r["parent_job_id"]) == jid]
    dependents = [int(r["id"]) for r in group_rows if r.get("depends_on_job_id") is not None and int(r["depends_on_job_id"]) == jid]
    return {
        "root_job_id": root_i,
        "parent_job_id": int(parent) if parent is not None else None,
        "depends_on_job_id": int(dep) if dep is not None else None,
        "child_job_ids": sorted(children),
        "dependent_job_ids": sorted(dependents),
        "is_root_of_group": root is None or int(root) == jid,
    }


def _downstream_job_ids(group_by_id: dict[int, dict[str, Any]], start_id: int) -> list[int]:
    """All jobs that transitively depend on ``start_id`` via ``depends_on_job_id`` edges within the group."""
    out: list[int] = []
    q: deque[int] = deque([start_id])
    seen: set[int] = {start_id}
    while q:
        u = q.popleft()
        for jid, row in group_by_id.items():
            d = row.get("depends_on_job_id")
            if d is None:
                continue
            if int(d) != u:
                continue
            if jid in seen:
                continue
            seen.add(jid)
            out.append(jid)
            q.append(jid)
    return out


def _build_stage_job_graph(job: dict[str, Any], items: list[dict[str, Any]], *, root_job_id: int) -> dict[str, Any]:
    """
    Minimal DAG over the **stage group** (``root_job_id``): edges follow ``depends_on_job_id``
    (producer -> consumer). Longest weighted path uses ``total_latency_ms`` per node (0 if null).
    """
    jid = int(job["id"])
    root_i = root_job_id
    if not items:
        return {
            "scope": "stage_group",
            "root_job_id": root_i,
            "anchor_job_id": jid,
            "nodes": [],
            "edges": [],
            "stage_dag": {"node_count": 0, "edges": []},
            "critical_path": None,
            "bottleneck": None,
            "failure_impact": [],
        }

    group_by_id = {int(r["id"]): dict(r) for r in items}
    ids = set(group_by_id.keys())

    nodes = [_timeline_node_summary(r) for r in items]
    edges: list[dict[str, Any]] = []
    for r in items:
        c = int(r["id"])
        d = r.get("depends_on_job_id")
        if d is None:
            continue
        d = int(d)
        if d not in ids:
            continue
        edges.append(
            {
                "from_job_id": d,
                "to_job_id": c,
                "kind": "depends_on",
            }
        )
        p = r.get("parent_job_id")
        if p is not None and int(p) == d:
            edges[-1]["aligned_parent"] = True

    # Longest weighted path (critical path in time) on DAG: weight = wall time per stage job
    indeg: dict[int, int] = {i: 0 for i in ids}
    succ: dict[int, list[int]] = {i: [] for i in ids}
    preds: dict[int, list[int]] = {i: [] for i in ids}
    for e in edges:
        u, v = int(e["from_job_id"]), int(e["to_job_id"])
        succ[u].append(v)
        preds[v].append(u)
        indeg[v] += 1

    qk = deque([i for i in ids if indeg[i] == 0])
    topo: list[int] = []
    while qk:
        u = qk.popleft()
        topo.append(u)
        for v in succ[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                qk.append(v)

    dag_ok = len(topo) == len(ids)
    weight = {i: _job_row_latency_ms(group_by_id[i]) for i in ids}

    critical_path: dict[str, Any] | None = None
    bottleneck: dict[str, Any] | None = None
    if dag_ok and topo:
        best: dict[int, int] = {}
        prev: dict[int, int | None] = {i: None for i in ids}
        for u in topo:
            if not preds[u]:
                best[u] = weight[u]
                prev[u] = None
            else:
                p0 = max(preds[u], key=lambda p: best[p])
                best[u] = weight[u] + best[p0]
                prev[u] = p0
        end = max(ids, key=lambda i: best[i])
        path_rev: list[int] = []
        cur: int | None = end
        visited_guard = 0
        while cur is not None and visited_guard < len(ids) + 2:
            path_rev.append(cur)
            cur = prev[cur]
            visited_guard += 1
        path = list(reversed(path_rev))
        total_cp = best[end]
        path_weights = [weight[j] for j in path]
        if path_weights:
            bi = max(range(len(path)), key=lambda i: path_weights[i])
            bottleneck = {
                "job_id": path[bi],
                "total_latency_ms": path_weights[bi],
                "share_of_critical_path": (path_weights[bi] / total_cp) if total_cp > 0 else None,
            }
        critical_path = {
            "job_ids": path,
            "total_ms": total_cp,
            "method": "weighted_longest_path_depends_on_dag",
            "note": "latency per node summed on the longest-weight path (not wall-clock overlap)",
        }
        cp_set = set(path)
        for n in nodes:
            if int(n["job_id"]) in cp_set:
                n["on_critical_path"] = True
            else:
                n["on_critical_path"] = False
    elif not dag_ok:
        critical_path = {
            "job_ids": [],
            "total_ms": None,
            "method": None,
            "note": "cycle_or_missing_toposort — graph may have a cycle or inconsistent depends_on",
        }

    failure_impact: list[dict[str, Any]] = []
    for i in ids:
        st = str(group_by_id[i].get("status") or "")
        if st not in _FAILED_STATUSES:
            continue
        blocked = _downstream_job_ids(group_by_id, i)
        failure_impact.append(
            {
                "failed_job_id": i,
                "status": st,
                "blocks_downstream_job_ids": blocked,
                "downstream_count": len(blocked),
            }
        )

    stage_dag_edges = [{"from": e["from_job_id"], "to": e["to_job_id"]} for e in edges]
    return {
        "scope": "stage_group",
        "root_job_id": root_i,
        "anchor_job_id": jid,
        "nodes": nodes,
        "edges": edges,
        "stage_dag": {"node_count": len(ids), "edges": stage_dag_edges},
        "critical_path": critical_path,
        "bottleneck": bottleneck,
        "failure_impact": failure_impact,
    }


def _parse_json_obj(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return {}
    return val if isinstance(val, dict) else {}


def _build_infra_job_timeline(conn: Any, *, job_id: int) -> dict[str, Any] | None:
    """
    Load job, events, model runs, optional worker, artifacts; build sorted ``spans`` + ``time_window``.
    """
    from utils.luma_brain import (
        enrich_job_with_success_artifacts,
        get_job,
        list_job_ids_by_trace_id,
        list_model_runs_for_job,
    )

    job = get_job(conn, job_id=job_id)
    if job is None:
        return None
    job = enrich_job_with_success_artifacts(conn, job)

    ev_rows = conn.execute(
        """
        SELECT id, job_id, from_status, to_status, created_at, message, payload_json
        FROM job_events
        WHERE job_id = ?
        ORDER BY created_at ASC, id ASC
        LIMIT 2000
        """,
        (job_id,),
    ).fetchall()
    events = [dict(r) for r in ev_rows]

    succeeded_events_by_id: dict[int, dict[str, Any]] = {}
    for e in events:
        if str(e.get("to_status") or "") != "SUCCEEDED":
            continue
        eid = e.get("id")
        if eid is not None:
            succeeded_events_by_id[int(eid)] = e

    raw_runs = list_model_runs_for_job(conn, job_id=job_id, limit=200)
    model_runs = list(reversed(raw_runs))
    model_runs = _hydrate_model_runs_with_attempts(conn, model_runs)
    for mr in model_runs:
        mr["scope_namespace"] = job.get("namespace")
        mr["scope_project_key"] = job.get("project_key")

    tid = job.get("trace_id")
    tid_s = str(tid).strip() if tid else ""
    related_ids: list[int] = [int(job_id)]
    if tid_s:
        related_ids = list_job_ids_by_trace_id(conn, trace_id=tid_s)

    worker: dict[str, Any] | None = None
    wk = job.get("worker_id")
    if wk is not None:
        wr = conn.execute(
            """
            SELECT id, worker_name, worker_type, status, last_heartbeat, capacity, inflight, created_at, updated_at
            FROM workers
            WHERE id = ?
            """,
            (int(wk),),
        ).fetchone()
        if wr is not None:
            worker = dict(wr)

    artifacts: list[dict[str, Any]] = []
    primary_artifact: dict[str, Any] | None = None
    oa = job.get("output_artifacts")
    if isinstance(oa, list):
        artifacts = [a for a in oa if isinstance(a, dict)]
    pa = job.get("primary_artifact")
    if isinstance(pa, dict):
        primary_artifact = pa

    # Provider / job-level ops context (queue + router hints on the row)
    prov_payload: dict[str, Any] = {}
    raw_payload = job.get("payload_json")
    if raw_payload:
        try:
            parsed = json.loads(str(raw_payload)) if isinstance(raw_payload, str) else raw_payload
            if isinstance(parsed, dict) and len(parsed) <= 32:
                prov_payload = {k: parsed[k] for k in list(parsed.keys())[:32]}
        except (json.JSONDecodeError, TypeError):
            prov_payload = {}

    lineage = job.get("artifact_lineage")
    context: dict[str, Any] = {
        "queue_wait_ms": job.get("queue_wait_ms"),
        "preprocess_ms": job.get("preprocess_ms"),
        "inference_ms": job.get("inference_ms"),
        "postprocess_ms": job.get("postprocess_ms"),
        "total_latency_ms": job.get("total_latency_ms"),
        "job_provider": job.get("provider"),
        "job_model": job.get("model_name"),
        "namespace": job.get("namespace"),
        "project_key": job.get("project_key"),
        "error_code": job.get("error_code"),
        "error_message": (str(job.get("error_message") or ""))[:500] or None,
        "payload_json_preview": prov_payload,
        "artifact_lineage": lineage if isinstance(lineage, dict) else None,
    }

    spans_pre: list[dict[str, Any]] = []
    for ev in events:
        fs = ev.get("from_status")
        tsto = ev.get("to_status")
        msg = ev.get("message")
        if msg:
            label = str(msg)[:200]
        else:
            label = f"{fs or '∅'} → {tsto or '∅'}"
        spans_pre.append(
            {
                "id": f"je-{ev['id']}",
                "kind": "job_event",
                "ts": int(ev["created_at"]),
                "label": label,
                "from_status": str(fs) if fs is not None else None,
                "to_status": str(tsto) if tsto is not None else None,
                "duration_ms": None,
                "queue_wait_ms": None,
                "meta": {
                    "event_id": ev.get("id"),
                },
            }
        )
    for mr in model_runs:
        mrid = int(mr.get("id") or 0)
        prov = (mr.get("primary_provider") or mr.get("provider") or "") or "?"
        mod = (mr.get("final_model") or mr.get("primary_model") or mr.get("model_name") or "") or "?"
        st = str(mr.get("status") or "")
        label = f"Inference {mrid} · {st} · {prov}/{mod}"
        dur: int | None = None
        for key in ("end_to_end_latency_ms", "latency_ms", "provider_latency_ms"):
            v = mr.get(key)
            if v is not None:
                try:
                    dur = max(0, int(v))
                    break
                except (TypeError, ValueError):
                    pass
        meta = {
            k: mr.get(k)
            for k in (
                "status",
                "provider",
                "model_name",
                "primary_provider",
                "fallback_used",
                "degraded",
                "error_type",
                "error_message",
                "outcome_attribution",
            )
            if k in mr
        }
        meta["scope_namespace"] = job.get("namespace")
        meta["scope_project_key"] = job.get("project_key")
        if mr.get("attempts"):
            meta["attempts"] = mr["attempts"]
        spans_pre.append(
            {
                "id": f"mr-{mrid}",
                "kind": "model_run",
                "ts": int(mr.get("created_at") or 0),
                "label": label,
                "from_status": None,
                "to_status": None,
                "duration_ms": dur,
                "queue_wait_ms": int(mr["queue_wait_ms"]) if mr.get("queue_wait_ms") is not None else None,
                "meta": meta,
            }
        )
        for att in mr.get("attempts") or []:
            seq = att.get("seq")
            role = att.get("role") or "?"
            prov_a = att.get("provider_id") or "?"
            mod_a = att.get("model_name") or ""
            ok_a = att.get("ok")
            lat_a = att.get("latency_ms")
            et_a = att.get("error_type")
            st_a = "ok" if ok_a else "fail"
            label_a = f"Infer #{seq} · {role} · {st_a} · {prov_a}/{mod_a}"
            spans_pre.append(
                {
                    "id": f"mr-{mrid}-att-{seq if seq is not None else 'x'}",
                    "kind": "inference_attempt",
                    "ts": int(mr.get("created_at") or 0),
                    "label": label_a[:200],
                    "from_status": None,
                    "to_status": None,
                    "duration_ms": int(lat_a) if lat_a is not None else None,
                    "queue_wait_ms": None,
                    "meta": {k: att.get(k) for k in ("seq", "role", "provider_id", "model_name", "ok", "latency_ms", "error_type", "error_message", "primary_skipped") if att.get(k) is not None or k == "ok"},
                }
            )
    for i, a in enumerate(artifacts):
        gen = a.get("generated_at")
        ts = int(gen) if gen is not None else int(job.get("updated_at") or 0)
        kind = str(a.get("kind") or "artifact")
        aid = a.get("artifact_id")
        span_id = f"art-{aid}" if aid is not None else f"art-{i}"
        meta = {
            "path": a.get("path"),
            "kind": kind,
            "taxonomy": a.get("taxonomy"),
            "role": a.get("role"),
            "category": (a.get("metadata") or {}).get("category")
            if isinstance(a.get("metadata"), dict)
            else a.get("category"),
            "is_primary": a.get("is_primary"),
            "artifact_id": aid,
            "stage": a.get("stage"),
            "source": a.get("source"),
            "job_event_id": a.get("job_event_id"),
        }
        jev = a.get("job_event_id")
        if jev is not None:
            jev_i = int(jev)
            se = succeeded_events_by_id.get(jev_i)
            if se is not None:
                meta["ledger_link"] = "succeeded_job_event"
                meta["ledger_event_created_at"] = int(se.get("created_at") or 0)
        spans_pre.append(
            {
                "id": span_id,
                "kind": "artifact",
                "ts": ts,
                "label": f"{kind}",
                "from_status": None,
                "to_status": None,
                "duration_ms": None,
                "queue_wait_ms": None,
                "meta": {k: v for k, v in meta.items() if v is not None},
            }
        )

    def _span_sort_key(s: dict[str, Any]) -> tuple:
        kind = s.get("kind") or ""
        if kind == "job_event":
            k = 0
        elif kind == "model_run":
            k = 1
        elif kind == "inference_attempt":
            k = 2
        else:
            k = 3
        return (s.get("ts") or 0, k, s.get("id") or "")

    spans_pre.sort(key=_span_sort_key)
    t_candidates: list[int] = [s["ts"] for s in spans_pre if s.get("ts")]
    for s in spans_pre:
        if s.get("duration_ms") and s.get("ts"):
            t_candidates.append(int(s["ts"]) + max(0, int(s["duration_ms"]) // 1000))
    t0 = min(t_candidates) if t_candidates else int(job.get("created_at") or 0)
    t1 = max(t_candidates) if t_candidates else t0
    if t1 <= t0:
        t1 = t0 + 1

    out_job = dict(job)
    from utils.luma_brain import list_jobs_for_stage_group

    root_eff = out_job.get("root_job_id")
    root_i = int(root_eff) if root_eff is not None else int(job_id)
    group_rows = list_jobs_for_stage_group(conn, root_job_id=root_i)
    job_relationships = _relationships_for_job(out_job, group_rows)
    job_graph = _build_stage_job_graph(out_job, group_rows, root_job_id=root_i)
    return {
        "job": out_job,
        "project_scope": _project_scope_from_job(out_job),
        "trace_id": tid_s or None,
        "related_job_ids": related_ids,
        "events": events,
        "model_runs": [dict(m) for m in model_runs],
        "artifacts": artifacts,
        "primary_artifact": primary_artifact,
        "worker": worker,
        "context": context,
        "spans": spans_pre,
        "time_window": {
            "t0": t0,
            "t1": t1,
            "width_seconds": max(1, t1 - t0),
        },
        "job_relationships": job_relationships,
        "job_graph": job_graph,
    }


@router.get("/api/infra/jobs", response_model=InfraJobListResponse)
def infra_list_jobs(
    status: list[str] | None = Query(default=None),
    job_type: str | None = Query(default=None),
    worker_id: int | None = Query(default=None),
    session_id: int | None = Query(default=None),
    trace_id: str | None = Query(default=None, description="Substring match on jobs.trace_id"),
    namespace: str | None = Query(
        default=None,
        description="Filter jobs to this platform namespace; omit to list all (legacy behavior).",
    ),
    project_key: str | None = Query(
        default=None,
        description="Filter jobs to this project key within a namespace; combine with namespace for full scope.",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    from utils.luma_brain import list_jobs

    conn = _with_conn()
    try:
        items = list_jobs(
            conn,
            statuses=status or None,
            job_type=job_type,
            worker_id=worker_id,
            session_id=session_id,
            trace_id=trace_id,
            namespace=namespace,
            project_key=project_key,
            limit=limit,
            offset=offset,
        )
        return {
            "count": len(items),
            "offset": offset,
            "limit": limit,
            "items": items,
        }
    finally:
        conn.close()


@router.get("/api/infra/jobs/{job_id}", response_model=InfraJobDetailResponse)
def infra_get_job(job_id: int):
    from utils.luma_brain import enrich_job_with_success_artifacts, get_job, list_model_runs_for_job

    conn = _with_conn()
    try:
        job = get_job(conn, job_id=job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        job = enrich_job_with_success_artifacts(conn, job)
        events = conn.execute(
            """
            SELECT id, job_id, from_status, to_status, created_at, message, payload_json
            FROM job_events
            WHERE job_id = ?
            ORDER BY created_at ASC, id ASC
            LIMIT 1000
            """,
            (job_id,),
        ).fetchall()
        model_runs = _hydrate_model_runs_with_attempts(conn, list_model_runs_for_job(conn, job_id=job_id, limit=100))
        for mr in model_runs:
            mr["scope_namespace"] = job.get("namespace")
            mr["scope_project_key"] = job.get("project_key")
        arts = job.get("output_artifacts")
        arts_list = [a for a in arts] if isinstance(arts, list) else []
        prim = job.get("primary_artifact") if isinstance(job.get("primary_artifact"), dict) else None
        return {
            "job": job,
            "project_scope": _project_scope_from_job(job),
            "events": [dict(r) for r in events],
            "model_runs": model_runs,
            "artifacts": [a for a in arts_list if isinstance(a, dict)],
            "primary_artifact": prim,
        }
    finally:
        conn.close()


@router.get(
    "/api/infra/jobs/{job_id}/timeline",
    response_model=InfraJobTimelineResponse,
)
def infra_get_job_timeline(job_id: int):
    """
    One consolidated view: job row, job_events, model_runs, output artifacts, worker,
    and a merged, sorted ``spans`` list for a simple UI waterfall.
    """
    conn = _with_conn()
    try:
        body = _build_infra_job_timeline(conn, job_id=job_id)
        if body is None:
            raise HTTPException(status_code=404, detail="job not found")
        return body
    finally:
        conn.close()


@router.get(
    "/api/infra/traces/{trace_id}",
    response_model=InfraTraceLookupResponse,
)
def infra_get_trace_by_id(trace_id: str):
    """
    Resolve a distributed ``trace_id`` to the anchor (lowest) job in that trace and
    return the same payload as ``GET .../jobs/{job_id}/timeline`` plus ``job_ids`` / ``anchor_job_id``.
    """
    from utils.luma_brain import list_job_ids_by_trace_id

    raw = (trace_id or "").strip()
    if not raw:
        raise HTTPException(status_code=404, detail="trace not found")
    conn = _with_conn()
    try:
        jids = list_job_ids_by_trace_id(conn, trace_id=raw)
        if not jids:
            raise HTTPException(status_code=404, detail="trace not found")
        anchor = jids[0]
        body = _build_infra_job_timeline(conn, job_id=anchor)
        if body is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {
            **body,
            "anchor_job_id": int(anchor),
            "job_ids": jids,
        }
    finally:
        conn.close()


@router.get("/api/infra/jobs/{job_id}/stages")
def infra_get_job_stages(job_id: int):
    """
    List all jobs in a **stage group**: same ``root_job_id`` (or the given id as root).
    Use for ``PIPELINE_STAGE`` linear graphs; legacy single jobs return a one-row list.
    """
    from utils.luma_brain import get_job, list_jobs_for_stage_group

    conn = _with_conn()
    try:
        job = get_job(conn, job_id=job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        root = job.get("root_job_id")
        if root is None:
            root = int(job["id"])
        else:
            root = int(root)
        items = list_jobs_for_stage_group(conn, root_job_id=root)
        return {
            "root_job_id": root,
            "requested_job_id": job_id,
            "count": len(items),
            "items": items,
        }
    finally:
        conn.close()


@router.post("/api/infra/jobs/{job_id}/retry", response_model=InfraJobActionResponse)
def infra_retry_job(job_id: int, _ops: None = Depends(require_ops_auth)):
    """
    Manual retry: re-queue with **attempt reset to 0** and cleared worker claim / errors.

    Allowed from ``DEAD_LETTERED``, ``FAILED_RETRYABLE``, ``FAILED_PERMANENT``, ``SUCCEEDED``,
    ``CANCELLED``. Not allowed from ``QUEUED`` or in-flight active states.
    """
    from utils.luma_brain import get_job, manual_retry_job

    conn = _with_conn()
    try:
        out = manual_retry_job(conn, job_id=job_id, source="infra_api")
        if not out["ok"]:
            if out.get("message") == "job not found":
                raise HTTPException(status_code=404, detail="job not found")
            raise HTTPException(status_code=400, detail=out.get("message") or "retry not allowed")
        updated = get_job(conn, job_id=job_id)
        return {
            "ok": True,
            "job_id": job_id,
            "status": (updated or {}).get("status"),
            "message": out.get("message"),
        }
    finally:
        conn.close()


@router.post("/api/infra/jobs/{job_id}/cancel", response_model=InfraJobActionResponse)
def infra_cancel_job(job_id: int, _ops: None = Depends(require_ops_auth)):
    from utils.luma_brain import get_job, update_job_status

    conn = _with_conn()
    try:
        job = get_job(conn, job_id=job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        cur = str(job.get("status") or "")
        if cur in {"SUCCEEDED", "FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"}:
            return {"ok": True, "job_id": job_id, "status": cur, "message": "job already terminal"}
        update_job_status(
            conn,
            job_id=job_id,
            to_status="CANCELLED",
            message="manual cancel from infra api",
            payload={"source": "infra_api"},
        )
        updated = get_job(conn, job_id=job_id)
        return {"ok": True, "job_id": job_id, "status": (updated or {}).get("status")}
    finally:
        conn.close()


@router.get("/api/infra/worker-pools", response_model=InfraWorkerPoolsResponse)
def infra_worker_pools():
    """
    Logical **executor pools**: remaining admission slots per routing class (``workers.capacity`` minus live
    pipeline-active ``jobs`` on ONLINE workers), plus SSOT worker rows grouped by ``workers.worker_type``.
    Operators set ``LIVEHOUSE_EXECUTOR_CLASS`` on Celery workers; jobs derive a required pool from job type /
    stage (see ``services.worker_pools`` module doc).
    """
    from services.worker_pools import JOB_ROUTING_EXECUTOR_CLASSES, KNOWN_EXECUTOR_CLASSES
    from utils.luma_brain import executor_pool_headroom_for_dispatch

    conn = _with_conn()
    try:
        headroom = executor_pool_headroom_for_dispatch(conn)
        rows = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(worker_type), ''), '(blank)') AS executor_pool, COUNT(*) AS c
            FROM workers
            GROUP BY COALESCE(NULLIF(TRIM(worker_type), ''), '(blank)')
            ORDER BY executor_pool ASC
            """
        ).fetchall()
        return {
            "routing_executor_classes": list(JOB_ROUTING_EXECUTOR_CLASSES),
            "known_executor_classes": list(KNOWN_EXECUTOR_CLASSES),
            "executor_pool_headroom": headroom,
            "workers_by_executor_pool": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/api/infra/workers", response_model=InfraWorkersResponse)
def infra_list_workers():
    from infra.celery_inspect import celery_worker_inspect_snapshot, enrich_worker_rows_with_broker
    from utils.luma_brain import mark_stale_workers_offline

    conn = _with_conn()
    try:
        mark_stale_workers_offline(conn)
        rows = conn.execute(
            """
            SELECT id, worker_name, worker_type, status, last_heartbeat, capacity, inflight, created_at, updated_at
            FROM workers
            ORDER BY updated_at DESC, id DESC
            LIMIT 1000
            """
        ).fetchall()
        items = [dict(r) for r in rows]
        broker_snap = celery_worker_inspect_snapshot()
        enriched, unmatched = enrich_worker_rows_with_broker(items, broker_snap)
        return {
            "count": len(enriched),
            "items": enriched,
            "broker": {
                "celery_unavailable": bool(broker_snap.get("celery_unavailable")),
                "worker_count": int(broker_snap.get("worker_count") or 0),
                "error": broker_snap.get("error"),
            },
            "unmatched_broker_workers": unmatched,
        }
    finally:
        conn.close()


@router.post("/api/infra/workers/{worker_id}/pause", response_model=InfraWorkerActionResponse)
def infra_worker_pause(worker_id: int, _ops: None = Depends(require_ops_auth)):
    """
    Set worker to ``PAUSED``: no new job claims; existing inflight work should finish or be requeued
    by maintenance if the process dies. Heartbeat does not override this state.
    """
    from utils.luma_brain import set_worker_control_status

    conn = _with_conn()
    try:
        out = set_worker_control_status(conn, worker_id=worker_id, to_status="PAUSED")
        if not out["ok"]:
            if out.get("message") == "worker not found":
                raise HTTPException(status_code=404, detail="worker not found")
            raise HTTPException(status_code=400, detail=out.get("message") or "pause failed")
        return out
    finally:
        conn.close()


@router.post("/api/infra/workers/{worker_id}/resume", response_model=InfraWorkerActionResponse)
def infra_worker_resume(worker_id: int, _ops: None = Depends(require_ops_auth)):
    """Set worker to ``ONLINE`` (new claims and dispatch headroom allowed when capacity permits)."""
    from utils.luma_brain import set_worker_control_status

    conn = _with_conn()
    try:
        out = set_worker_control_status(conn, worker_id=worker_id, to_status="ONLINE")
        if not out["ok"]:
            if out.get("message") == "worker not found":
                raise HTTPException(status_code=404, detail="worker not found")
            raise HTTPException(status_code=400, detail=out.get("message") or "resume failed")
        return out
    finally:
        conn.close()


@router.post("/api/infra/workers/{worker_id}/drain", response_model=InfraWorkerActionResponse)
def infra_worker_drain(worker_id: int, _ops: None = Depends(require_ops_auth)):
    """
    Set worker to ``DRAINING``: do not claim new work; run existing inflight to completion.
    Dispatch/headroom count only ``ONLINE`` capacity, so this worker stops receiving new ``run_job`` work.
    """
    from utils.luma_brain import set_worker_control_status

    conn = _with_conn()
    try:
        out = set_worker_control_status(conn, worker_id=worker_id, to_status="DRAINING")
        if not out["ok"]:
            if out.get("message") == "worker not found":
                raise HTTPException(status_code=404, detail="worker not found")
            raise HTTPException(status_code=400, detail=out.get("message") or "drain failed")
        return out
    finally:
        conn.close()


@router.get("/api/infra/providers", response_model=InfraProvidersResponse)
def infra_list_providers():
    from inference.providers.registry import build_providers_catalog
    from utils.config_loader import ConfigLoader

    cfg = ConfigLoader.load()
    model_cfg = cfg.get("model", {}) if isinstance(cfg, dict) else {}
    active = str(model_cfg.get("provider", "ollama")).strip().lower()
    return build_providers_catalog(model_cfg, active_provider=active)


@router.get("/api/infra/runtime-stream", response_model=InfraRuntimeStreamResponse)
def infra_runtime_stream(events_limit: int = Query(default=80, ge=1, le=500)):
    """
    Recent ``job_events`` (newest last) plus per-stage job status aggregates for the runtime UI.
    """
    conn = _with_conn()
    try:
        ev_rows = conn.execute(
            """
            SELECT je.id, je.job_id, je.from_status, je.to_status, je.created_at, je.message,
                   j.stage_name, j.trace_id, j.worker_id, w.worker_name
            FROM job_events je
            LEFT JOIN jobs j ON j.id = je.job_id
            LEFT JOIN workers w ON w.id = j.worker_id
            ORDER BY je.created_at DESC, je.id DESC
            LIMIT ?
            """,
            (events_limit,),
        ).fetchall()
        events = [dict(r) for r in reversed(ev_rows)]

        stage_rows = conn.execute(
            """
            SELECT
              COALESCE(NULLIF(TRIM(stage_name), ''), NULLIF(TRIM(job_type), ''), '(legacy)') AS stage_key,
              status,
              COUNT(*) AS c,
              AVG(CASE WHEN total_latency_ms IS NOT NULL THEN total_latency_ms END) AS avg_latency_ms
            FROM jobs
            GROUP BY stage_key, status
            ORDER BY stage_key ASC, status ASC
            """
        ).fetchall()
        stages: list[dict[str, Any]] = []
        for r in stage_rows:
            avg = r["avg_latency_ms"]
            stages.append(
                {
                    "stage_key": str(r["stage_key"]),
                    "status": str(r["status"]),
                    "count": int(r["c"]),
                    "avg_latency_ms": int(round(avg)) if avg is not None else None,
                }
            )

        retry_rows = conn.execute(
            """
            SELECT je.id, je.job_id, je.from_status, je.to_status, je.created_at, je.message,
                   j.stage_name, j.trace_id, j.worker_id, w.worker_name
            FROM job_events je
            LEFT JOIN jobs j ON j.id = je.job_id
            LEFT JOIN workers w ON w.id = j.worker_id
            WHERE LOWER(COALESCE(je.message, '')) LIKE '%retry%'
               OR (je.to_status = 'QUEUED' AND je.from_status IN ('FAILED_RETRYABLE', 'DEAD_LETTERED'))
            ORDER BY je.created_at DESC, je.id DESC
            LIMIT 12
            """,
        ).fetchall()
        retries_recent = [dict(r) for r in reversed(retry_rows)]

        return {
            "events": events,
            "stages": stages,
            "retries_recent": retries_recent,
        }
    finally:
        conn.close()


@router.get("/api/infra/brain", response_model=InfraBrainDashboardResponse)
def infra_brain_dashboard(
    sessions_limit: int = Query(default=25, ge=1, le=200),
    photos_limit: int = Query(default=50, ge=1, le=500),
):
    """
    Read-only snapshot of ``luma_brain.db``: per-table row counts, ``photos`` / ``sessions`` samples.

    For job/worker queue metrics use ``GET /api/infra/metrics``; for a single job use timeline APIs.
    """
    from utils.luma_brain import collect_brain_dashboard

    conn = _with_conn()
    try:
        return collect_brain_dashboard(
            conn, sessions_limit=sessions_limit, photos_limit=photos_limit
        )
    finally:
        conn.close()


_PIPELINE_ACTIVE_STATUSES = frozenset(
    {"CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"}
)


def _build_metric_sample(metrics: dict[str, Any]) -> dict[str, Any]:
    """Compact, persistable point derived from a full metrics snapshot (real values only)."""
    by_status = ((metrics.get("jobs") or {}).get("by_status")) or {}
    admission = ((metrics.get("workers") or {}).get("pipeline_admission")) or {}
    latency = ((metrics.get("latency") or {}).get("total_latency_ms")) or {}

    inflight = int(admission.get("total_inflight") or 0)
    capacity = int(admission.get("total_capacity") or 0)
    util_pct: int | None
    if capacity > 0:
        util_pct = min(100, round(inflight / capacity * 100))
    elif inflight > 0:
        util_pct = 100
    else:
        util_pct = None

    running = sum(int(by_status.get(s) or 0) for s in _PIPELINE_ACTIVE_STATUSES)
    failed_total = (
        int(by_status.get("FAILED_RETRYABLE") or 0)
        + int(by_status.get("FAILED_PERMANENT") or 0)
        + int(by_status.get("DEAD_LETTERED") or 0)
    )
    return {
        "queued": int(by_status.get("QUEUED") or 0),
        "running": running,
        "failed_total": failed_total,
        "succeeded_cumulative": int(by_status.get("SUCCEEDED") or 0),
        "util_pct": util_pct,
        "p50_ms": latency.get("p50"),
        "p95_ms": latency.get("p95"),
    }


@router.get("/api/infra/metrics/history", response_model=InfraMetricsHistoryResponse)
def infra_metrics_history(
    window_sec: int = Query(default=3600, ge=60, le=86400),
    limit: int = Query(default=240, ge=1, le=2000),
):
    """
    Recent persisted control-plane samples (oldest first) with derived ``throughput_per_min``.

    Samples are written opportunistically (throttled) by ``GET /api/infra/metrics``.
    """
    import time

    from utils.luma_brain import list_infra_metric_samples

    conn = _with_conn()
    try:
        now = int(time.time())
        rows = list_infra_metric_samples(conn, since_sec=now - int(window_sec), limit=limit)
    finally:
        conn.close()

    points: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    for r in rows:
        tput: float | None = None
        if prev is not None:
            dt = int(r["ts"]) - int(prev["ts"])
            delta = int(r.get("succeeded_cumulative") or 0) - int(prev.get("succeeded_cumulative") or 0)
            if dt > 0 and delta >= 0:
                tput = round(delta / dt * 60, 1)
        points.append({**r, "throughput_per_min": tput})
        prev = r
    return {"count": len(points), "window_sec": int(window_sec), "points": points}


@router.get("/api/infra/metrics", response_model=InfraMetricsResponse)
def infra_metrics(
    namespace: str | None = Query(
        default=None,
        description="When set, job and model_runs SQL aggregates are limited to this namespace (default='default' if omitted in DB).",
    ),
    project_key: str | None = Query(
        default=None,
        description="Optional; combine with namespace to scope to one logical product.",
    ),
):
    from infra.metrics import collect_infra_metrics

    conn = _with_conn()
    try:
        metrics = collect_infra_metrics(conn, namespace=namespace, project_key=project_key)
        # Persist a compact, global time-series sample (throttled) so trends survive reloads.
        # Best-effort only; never let sampling break the read path.
        if namespace is None and project_key is None:
            try:
                from utils.luma_brain import record_infra_metric_sample

                record_infra_metric_sample(conn, payload=_build_metric_sample(metrics))
            except Exception:
                pass
        return metrics
    finally:
        conn.close()


@router.get("/api/infra/cost")
def infra_cost(
    window_hours: float = Query(
        default=168.0, ge=0.5, le=8760.0, description="Lookback window in hours; default 7 days"
    ),
    input_usd_per_mtok: float = Query(
        default=0.5, ge=0.0, le=10_000.0, description="Pricing: USD per 1M prompt tokens"
    ),
    output_usd_per_mtok: float = Query(
        default=1.5, ge=0.0, le=10_000.0, description="Pricing: USD per 1M completion tokens"
    ),
):
    """Token usage and estimated cost from the ``model_runs`` ledger.

    Cost is **derived at query time** from token counts × the configurable per-million-token
    prices, so re-pricing never requires a DB backfill.  Rows with NULL token columns
    (Ollama without token reporting, or mock provider) contribute to run counts and latency
    aggregates but are excluded from token / cost sums — ``token_coverage_pct`` exposes this gap.

    **Pricing defaults** ($0.50 / $1.50 per MTok input/output) are representative of mid-tier
    commercial APIs for comparison purposes.  Pass ``input_usd_per_mtok=0&output_usd_per_mtok=0``
    for pure GPU-hour attribution (token throughput only).
    """
    import time

    from utils.luma_brain import brain_connect, summarize_model_run_costs

    since_ts = int(time.time()) - int(window_hours * 3600)
    conn = brain_connect()
    try:
        totals_list = summarize_model_run_costs(
            conn,
            since_ts=since_ts,
            input_usd_per_mtok=input_usd_per_mtok,
            output_usd_per_mtok=output_usd_per_mtok,
            group_by_model=False,
        )
        by_model_list = summarize_model_run_costs(
            conn,
            since_ts=since_ts,
            input_usd_per_mtok=input_usd_per_mtok,
            output_usd_per_mtok=output_usd_per_mtok,
            group_by_model=True,
        )
    finally:
        conn.close()

    totals = totals_list[0] if totals_list else {}
    total_runs = int(totals.get("runs") or 0)
    with_tokens = int(totals.get("runs_with_token_usage") or 0)

    return {
        "window_hours": window_hours,
        "since_ts": since_ts,
        "pricing": {
            "input_usd_per_mtok": input_usd_per_mtok,
            "output_usd_per_mtok": output_usd_per_mtok,
        },
        "totals": totals,
        "by_model": by_model_list,
        "token_coverage_pct": round(with_tokens / total_runs * 100, 1) if total_runs else 0.0,
    }


@router.get("/api/infra/dead-letter", response_model=InfraJobListResponse)
def infra_dead_letter(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    namespace: str | None = Query(default=None),
    project_key: str | None = Query(default=None),
):
    """
    Jobs that **exhausted automatic retries** (``DEAD_LETTERED``): not claimable until
    ``POST .../retry`` or a new job is created. Sorted by ``updated_at`` descending.

    For operator-config / bad-input failures see ``FAILED_PERMANENT`` via ``GET /api/infra/jobs``.
    """
    from utils.luma_brain import list_jobs

    conn = _with_conn()
    try:
        items = list_jobs(
            conn,
            statuses=["DEAD_LETTERED"],
            namespace=namespace,
            project_key=project_key,
            limit=limit,
            offset=offset,
            sort="updated_at",
        )
        return {
            "count": len(items),
            "offset": offset,
            "limit": limit,
            "items": items,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# RLHF: pairwise voting + Bradley-Terry reward model
# ---------------------------------------------------------------------------

class RLHFVoteRequest(BaseModel):
    winner_path: str = Field(..., description="Absolute file path of the preferred image")
    loser_path: str = Field(..., description="Absolute file path of the non-preferred image")
    session_key: str | None = Field(None, description="Optional session scope")
    voter_id: str | None = None


@router.post("/api/rlhf/vote")
def rlhf_submit_vote(body: RLHFVoteRequest, _ops: None = Depends(require_ops_auth)):
    """Record one pairwise human preference vote."""
    from pathlib import Path

    from utils.http_security import path_is_under_roots
    from utils.rlhf import record_vote

    if body.winner_path == body.loser_path:
        raise HTTPException(status_code=400, detail="winner_path and loser_path must differ")
    try:
        from api.gallery_routes import _gallery_path_roots

        roots = _gallery_path_roots()
    except Exception:
        roots = []
    for label, raw in (("winner_path", body.winner_path), ("loser_path", body.loser_path)):
        try:
            p = Path(raw).expanduser().resolve(strict=False)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"invalid {label}") from exc
        if not p.is_file() or (roots and not path_is_under_roots(p, roots)):
            raise HTTPException(status_code=403, detail=f"{label} not allowed")
    conn = _with_conn()
    try:
        vote_id = record_vote(
            conn,
            winner_path=str(Path(body.winner_path).expanduser().resolve()),
            loser_path=str(Path(body.loser_path).expanduser().resolve()),
            session_key=body.session_key,
            source="manual",
            voter_id=body.voter_id,
        )
        return {"ok": True, "vote_id": vote_id}
    finally:
        conn.close()


@router.get("/api/rlhf/pair")
def rlhf_get_pair(session_key: str | None = Query(None)):
    """Return a random image pair for the user to compare."""
    from utils.rlhf import get_candidate_pair, get_vote_count, load_catalog_image_paths

    fallback_paths: list[str] = []
    try:
        from api.gallery_routes import _runtime_base_dir

        fallback_paths = load_catalog_image_paths(
            os.path.join(_runtime_base_dir(), "analysis_results.json")
        )
    except Exception:  # noqa: BLE001 - catalog is best-effort; voting still works without it
        fallback_paths = []

    conn = _with_conn()
    try:
        pair = get_candidate_pair(conn, session_key=session_key, fallback_paths=fallback_paths)
        total = get_vote_count(conn, session_key=session_key)
        return {"pair": list(pair) if pair else None, "total_votes": total}
    finally:
        conn.close()


@router.get("/api/rlhf/rankings")
def rlhf_rankings(
    session_key: str | None = Query(None),
    max_iter: int = Query(default=200, ge=10, le=2000),
):
    """
    Compute Bradley-Terry quality rankings for all images that have received
    at least one pairwise comparison vote.
    """
    from utils.rlhf import compute_bradley_terry, get_vote_count

    conn = _with_conn()
    try:
        rankings = compute_bradley_terry(conn, session_key=session_key, max_iter=max_iter)
        total_votes = get_vote_count(conn, session_key=session_key)
        return {
            "session_key": session_key,
            "total_votes": total_votes,
            "items": rankings,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Prompt A/B experiment framework
# ---------------------------------------------------------------------------

class PromptVariantUpsertRequest(BaseModel):
    name: str
    prompt_text: str
    description: str = ""
    variant_tag: str = "control"
    config_json: str | None = None
    active: bool = True


class ExperimentRunRequest(BaseModel):
    variant_id: int
    experiment_name: str = "default"
    model_run_id: int | None = None
    image_path: str | None = None
    vlm_score: float | None = None
    outcome: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int | None = None


@router.get("/api/experiments/variants")
def experiments_list_variants(active_only: bool = Query(default=True)):
    """List registered prompt variants."""
    from utils.rlhf import list_prompt_variants

    conn = _with_conn()
    try:
        return {"variants": list_prompt_variants(conn, active_only=active_only)}
    finally:
        conn.close()


@router.post("/api/experiments/variants")
def experiments_upsert_variant(body: PromptVariantUpsertRequest, _ops: None = Depends(require_ops_auth)):
    """Register or update a prompt variant."""
    from utils.rlhf import upsert_prompt_variant

    conn = _with_conn()
    try:
        vid = upsert_prompt_variant(
            conn,
            name=body.name,
            prompt_text=body.prompt_text,
            description=body.description,
            variant_tag=body.variant_tag,
            config_json=body.config_json,
            active=body.active,
        )
        return {"ok": True, "variant_id": vid}
    finally:
        conn.close()


@router.post("/api/experiments/runs")
def experiments_record_run(body: ExperimentRunRequest, _ops: None = Depends(require_ops_auth)):
    """Record one prompt experiment run."""
    from utils.rlhf import record_experiment_run

    conn = _with_conn()
    try:
        run_id = record_experiment_run(
            conn,
            variant_id=body.variant_id,
            experiment_name=body.experiment_name,
            model_run_id=body.model_run_id,
            image_path=body.image_path,
            vlm_score=body.vlm_score,
            outcome=body.outcome,
            prompt_tokens=body.prompt_tokens,
            completion_tokens=body.completion_tokens,
            latency_ms=body.latency_ms,
        )
        return {"ok": True, "run_id": run_id}
    finally:
        conn.close()


@router.get("/api/experiments/results")
def experiments_results(
    experiment_name: str = Query(default="default"),
    window_hours: float = Query(default=168.0, ge=0.5, le=8760.0),
):
    """Per-variant aggregate stats: avg_score, win_rate_vs_control, token usage, latency."""
    import time
    from utils.rlhf import summarize_experiment

    since_ts = int(time.time() - window_hours * 3600)
    conn = _with_conn()
    try:
        summary = summarize_experiment(conn, experiment_name=experiment_name, since_ts=since_ts)
        return {
            "experiment_name": experiment_name,
            "window_hours": window_hours,
            "variants": summary,
        }
    finally:
        conn.close()
