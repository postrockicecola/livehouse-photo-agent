"""Lightweight infra metrics aggregation (SQLite + in-process runtime counters).

Response fields are labeled via ``metrics_authority`` so API consumers can tell **database SSOT**
aggregates from **process-local** gauges (this Python interpreter only) and **broker** best-effort.
"""
from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from typing import Any

try:  # Optional dependency.
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

    _PROM_AVAILABLE = True
except Exception:  # pragma: no cover - optional runtime dependency
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"
    _PROM_AVAILABLE = False
    Counter = Gauge = Histogram = None  # type: ignore[assignment]
    generate_latest = None  # type: ignore[assignment]


_LOCK = threading.Lock()
_PROVIDER_REQUESTS: dict[str, int] = defaultdict(int)
_PROVIDER_FAILURES: dict[str, int] = defaultdict(int)
_PROVIDER_FALLBACKS: dict[str, int] = defaultdict(int)
_LATENCY_ALL_MS: deque[int] = deque(maxlen=2000)
_LATENCY_BY_PROVIDER_MS: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
_INFERENCE_Q_DEPTH: int = 0
_INFERENCE_Q_ACTIVE: int = 0
_INFERENCE_Q_MAX_INFLIGHT: int = 0
_INFERENCE_Q_NUM_WORKERS: int = 1
_INFERENCE_Q_E2E_MS: deque[int] = deque(maxlen=1000)
_INFERENCE_Q_BATCH_SIZES: deque[int] = deque(maxlen=500)
_INFERENCE_Q_QUEUE_WAIT_MS: deque[int] = deque(maxlen=1000)
_INFERENCE_Q_THROUGHPUT_RING: deque[tuple[float, int]] = deque(maxlen=400)
_INFERENCE_Q_BUSY_RING: deque[tuple[float, float]] = deque(maxlen=400)

_STAGE3_FF: dict[str, int] = {"fast_only": 0, "full": 0}

if _PROM_AVAILABLE:
    _P_PROVIDER_REQUESTS = Counter(
        "livehouse_provider_requests_total",
        "Provider requests total",
        ["provider"],
    )
    _P_PROVIDER_FAILURES = Counter(
        "livehouse_provider_failures_total",
        "Provider failures total",
        ["provider"],
    )
    _P_PROVIDER_FALLBACKS = Counter(
        "livehouse_provider_fallbacks_total",
        "Provider fallback total",
        ["provider"],
    )
    _P_INFERENCE_LATENCY_MS = Histogram(
        "livehouse_inference_latency_ms",
        "Inference latency in milliseconds",
        ["provider"],
        buckets=(50, 100, 200, 400, 800, 1500, 3000, 6000, 12000),
    )
    _P_JOBS_STATUS = Gauge(
        "livehouse_jobs_status_count",
        "Jobs grouped by status",
        ["status"],
    )
    _P_QUEUE_BACKLOG = Gauge(
        "livehouse_queue_backlog_count",
        "Queue backlog values",
        ["kind"],
    )
    _P_WORKERS = Gauge(
        "livehouse_workers_count",
        "Worker counters",
        ["kind"],
    )
    _P_INFERENCE_Q_DEPTH = Gauge(
        "livehouse_inference_queue_depth",
        "In-memory inference priority queue size (waiting jobs)",
    )
    _P_INFERENCE_Q_ACTIVE = Gauge(
        "livehouse_inference_queue_active",
        "Inference queue workers currently inside provider.generate",
    )
    _P_INFERENCE_Q_MAX = Gauge(
        "livehouse_inference_queue_max_inflight",
        "Configured max concurrent inference jobs (queued + running)",
    )
    _P_INFERENCE_Q_E2E = Histogram(
        "livehouse_inference_queue_job_ms",
        "Wall time from admission to job completion (queue + infer)",
        buckets=(50, 100, 200, 400, 800, 1500, 3000, 6000, 12000, 180000),
    )
    _P_STAGE3_FAST_FIRST = Counter(
        "livehouse_stage3_fast_first_routing_total",
        "Stage3 fast-first routing counts per batch increment",
        ["routing"],
    )


def record_stage3_early_exit_counts(*, fast_only: int, full: int) -> None:
    """Aggregate fast-only vs full dimensional passes after Stage3 fast-first routing."""
    fo = max(0, int(fast_only))
    fu = max(0, int(full))
    with _LOCK:
        _STAGE3_FF["fast_only"] += fo
        _STAGE3_FF["full"] += fu
    if _PROM_AVAILABLE:
        if fo:
            _P_STAGE3_FAST_FIRST.labels(routing="fast_only").inc(fo)
        if fu:
            _P_STAGE3_FAST_FIRST.labels(routing="full").inc(fu)


def stage3_fast_first_counters_snapshot() -> dict[str, Any]:
    """Process-lifetime aggregates for Prometheus / health introspection."""
    with _LOCK:
        fo = int(_STAGE3_FF["fast_only"])
        fu = int(_STAGE3_FF["full"])
    total = fo + fu
    ratio = (fo / total) if total else 0.0
    return {
        "fast_only_count": fo,
        "full_count": fu,
        "early_exit_ratio": ratio,
    }


def record_provider_request(provider: str) -> None:
    with _LOCK:
        _PROVIDER_REQUESTS[provider] += 1
    if _PROM_AVAILABLE:
        _P_PROVIDER_REQUESTS.labels(provider=provider).inc()


def record_provider_failure(provider: str) -> None:
    with _LOCK:
        _PROVIDER_FAILURES[provider] += 1
    if _PROM_AVAILABLE:
        _P_PROVIDER_FAILURES.labels(provider=provider).inc()


def record_provider_fallback(provider: str) -> None:
    with _LOCK:
        _PROVIDER_FALLBACKS[provider] += 1
    if _PROM_AVAILABLE:
        _P_PROVIDER_FALLBACKS.labels(provider=provider).inc()


def record_inference_latency(provider: str, latency_ms: int) -> None:
    ms = max(0, int(latency_ms))
    with _LOCK:
        _LATENCY_ALL_MS.append(ms)
        _LATENCY_BY_PROVIDER_MS[provider].append(ms)
    if _PROM_AVAILABLE:
        _P_INFERENCE_LATENCY_MS.labels(provider=provider).observe(ms)


def snapshot_inference_queue_metrics(
    *,
    depth: int | None = None,
    active: int | None = None,
    max_inflight: int | None = None,
    job_e2e_ms: int | None = None,
    num_workers: int | None = None,
    batch_size: int | None = None,
    queue_wait_ms: int | None = None,
    infer_wall_sec: float | None = None,
    images_completed: int | None = None,
) -> None:
    """Update last-known inference queue stats (priority queue + worker pool)."""
    global _INFERENCE_Q_DEPTH, _INFERENCE_Q_ACTIVE, _INFERENCE_Q_MAX_INFLIGHT, _INFERENCE_Q_NUM_WORKERS
    d = max(0, int(depth)) if depth is not None else None
    a = max(0, int(active)) if active is not None else None
    m = max(0, int(max_inflight)) if max_inflight is not None else None
    nw = max(1, int(num_workers)) if num_workers is not None else None
    with _LOCK:
        if d is not None:
            _INFERENCE_Q_DEPTH = d
        if a is not None:
            _INFERENCE_Q_ACTIVE = a
        if m is not None:
            _INFERENCE_Q_MAX_INFLIGHT = m
        if nw is not None:
            _INFERENCE_Q_NUM_WORKERS = nw
        if job_e2e_ms is not None:
            _INFERENCE_Q_E2E_MS.append(max(0, int(job_e2e_ms)))
        if batch_size is not None:
            _INFERENCE_Q_BATCH_SIZES.append(max(1, int(batch_size)))
        if queue_wait_ms is not None:
            _INFERENCE_Q_QUEUE_WAIT_MS.append(max(0, int(queue_wait_ms)))
        now_m = time.monotonic()
        if images_completed is not None and int(images_completed) > 0:
            _INFERENCE_Q_THROUGHPUT_RING.append((now_m, int(images_completed)))
        if infer_wall_sec is not None and float(infer_wall_sec) > 0:
            _INFERENCE_Q_BUSY_RING.append((now_m, float(infer_wall_sec)))
    if not _PROM_AVAILABLE:
        return
    if d is not None:
        _P_INFERENCE_Q_DEPTH.set(d)
    if a is not None:
        _P_INFERENCE_Q_ACTIVE.set(a)
    if m is not None:
        _P_INFERENCE_Q_MAX.set(m)
    if job_e2e_ms is not None:
        _P_INFERENCE_Q_E2E.observe(max(0, int(job_e2e_ms)))


def _ring_sum_recent(ring: deque[tuple[float, float | int]], *, window_sec: float) -> float | None:
    if not ring:
        return None
    newest = ring[-1][0]
    cutoff = newest - float(window_sec)
    return float(sum(float(v) for ts, v in ring if ts >= cutoff))


def inference_queue_runtime_snapshot() -> dict[str, Any]:
    with _LOCK:
        e2e = list(_INFERENCE_Q_E2E_MS)
        bs = list(_INFERENCE_Q_BATCH_SIZES)
        qw = list(_INFERENCE_Q_QUEUE_WAIT_MS)
        nw = int(_INFERENCE_Q_NUM_WORKERS)
        depth = int(_INFERENCE_Q_DEPTH)
        active = int(_INFERENCE_Q_ACTIVE)
        max_inf = int(_INFERENCE_Q_MAX_INFLIGHT)
        imgs_30 = _ring_sum_recent(_INFERENCE_Q_THROUGHPUT_RING, window_sec=30.0)
        busy_30 = _ring_sum_recent(_INFERENCE_Q_BUSY_RING, window_sec=30.0)
    avg_batch = int(sum(bs) / len(bs)) if bs else None
    avg_qwait = int(sum(qw) / len(qw)) if qw else None
    win = 30.0
    img_per_sec = (imgs_30 / win) if imgs_30 is not None and imgs_30 > 0 else None
    gpu_est = min(1.0, busy_30 / (win * float(nw))) if busy_30 is not None and busy_30 > 0 and nw > 0 else None
    out: dict[str, Any] = {
        "depth": depth,
        "active_workers": active,
        "max_inflight": max_inf,
        "num_workers": nw,
        "avg_job_e2e_ms": int(sum(e2e) / len(e2e)) if e2e else None,
        "last_job_e2e_ms": int(e2e[-1]) if e2e else None,
        "avg_batch_size": avg_batch,
        "avg_queue_wait_ms": avg_qwait,
        "throughput_img_per_sec_30s": round(img_per_sec, 4) if img_per_sec is not None else None,
        "gpu_util_estimate_30s": round(gpu_est, 4) if gpu_est is not None else None,
    }
    _merge_real_gpu_telemetry(out, fallback_estimate=gpu_est)
    return out


def _merge_real_gpu_telemetry(out: dict[str, Any], *, fallback_estimate: float | None) -> None:
    """Overlay real Apple-Silicon GPU readings (``scripts/gpu_telemetry_sampler.py``) when fresh.

    Adds ``gpu_util_real`` / ``gpu_util_source`` (``powermetrics`` | ``estimate`` | ``none``) plus
    ``gpu_freq_mhz`` / ``gpu_power_w`` / ``gpu_sample_age_sec``. Best-effort: any failure leaves the
    busy-time estimate untouched. ``gpu_util`` is the value the UI should display (real if present).
    """
    sample = None
    try:
        from infra.gpu_telemetry import read_latest_sample

        sample = read_latest_sample()
    except Exception:
        sample = None
    if sample is not None:
        out["gpu_util_real"] = round(float(sample["gpu_util"]), 4)
        out["gpu_util"] = out["gpu_util_real"]
        out["gpu_util_source"] = "powermetrics"
        out["gpu_sample_age_sec"] = sample.get("age_sec")
        if "gpu_freq_mhz" in sample:
            out["gpu_freq_mhz"] = sample["gpu_freq_mhz"]
        if "gpu_power_w" in sample:
            out["gpu_power_w"] = sample["gpu_power_w"]
    else:
        out["gpu_util_real"] = None
        out["gpu_util"] = fallback_estimate if fallback_estimate is not None else None
        out["gpu_util_source"] = "estimate" if fallback_estimate is not None else "none"


def percentile_nearest_rank(samples: list[int], p: float) -> int | None:
    if not samples:
        return None
    xs = sorted(max(0, int(x)) for x in samples)
    n = len(xs)
    k = int(math.ceil(float(p) * n)) - 1
    k = max(0, min(n - 1, k))
    return int(xs[k])


def inference_queue_periodic_window_snapshot(*, window_sec: float = 5.0) -> dict[str, Any]:
    """Rolling-window gauges for periodic inference-queue logs (process-local only)."""
    w = max(0.5, float(window_sec))
    with _LOCK:
        qw = list(_INFERENCE_Q_QUEUE_WAIT_MS)
        nw = int(_INFERENCE_Q_NUM_WORKERS)
        depth = int(_INFERENCE_Q_DEPTH)
        active = int(_INFERENCE_Q_ACTIVE)
        max_inf = int(_INFERENCE_Q_MAX_INFLIGHT)
        imgs = _ring_sum_recent(_INFERENCE_Q_THROUGHPUT_RING, window_sec=w)
        busy = _ring_sum_recent(_INFERENCE_Q_BUSY_RING, window_sec=w)
    tail = qw[-512:] if len(qw) > 512 else qw
    p95_qw = percentile_nearest_rank(tail, 0.95)
    inf_per_sec = (float(imgs) / w) if imgs is not None and float(imgs) > 0.0 else None
    gpu_u = (
        min(1.0, float(busy) / (w * float(nw)))
        if busy is not None and float(busy) > 0.0 and nw > 0
        else None
    )
    return {
        "window_sec": round(w, 3),
        "queue_size": depth,
        "inflight": active,
        "pending": depth + active,
        "p95_queue_wait_ms": p95_qw,
        "inference_per_sec": round(inf_per_sec, 4) if inf_per_sec is not None else None,
        "gpu_util": round(gpu_u, 4) if gpu_u is not None else None,
        "num_workers": nw,
        "max_inflight": max_inf,
    }


def prometheus_enabled() -> bool:
    return _PROM_AVAILABLE


def render_prometheus_metrics() -> tuple[bytes, str]:
    if not _PROM_AVAILABLE or generate_latest is None:  # pragma: no cover - optional runtime dependency
        return b"# prometheus_client not installed\n", CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST


def provider_runtime_metrics() -> dict[str, Any]:
    with _LOCK:
        providers = sorted(set(_PROVIDER_REQUESTS) | set(_PROVIDER_FAILURES) | set(_PROVIDER_FALLBACKS))
        items = []
        for p in providers:
            lat = list(_LATENCY_BY_PROVIDER_MS.get(p, ()))
            avg_ms = int(sum(lat) / len(lat)) if lat else None
            last_ms = lat[-1] if lat else None
            items.append(
                {
                    "provider": p,
                    "requests": int(_PROVIDER_REQUESTS.get(p, 0)),
                    "failures": int(_PROVIDER_FAILURES.get(p, 0)),
                    "fallbacks": int(_PROVIDER_FALLBACKS.get(p, 0)),
                    "avg_latency_ms": avg_ms,
                    "last_latency_ms": last_ms,
                }
            )
        all_lat = list(_LATENCY_ALL_MS)
    return {
        "providers": items,
        "avg_latency_ms": int(sum(all_lat) / len(all_lat)) if all_lat else None,
        "last_latency_ms": all_lat[-1] if all_lat else None,
    }


def queue_backlog_snapshot() -> dict[str, Any]:
    from celery import Celery
    import os

    broker = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
    backend = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")
    client = Celery("infra_metrics", broker=broker, backend=backend)
    try:
        inspect = client.control.inspect(timeout=1.0)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}
        workers = sorted(set(active.keys()) | set(reserved.keys()) | set(scheduled.keys()))
        total_active = sum(len(active.get(w, []) or []) for w in workers)
        total_reserved = sum(len(reserved.get(w, []) or []) for w in workers)
        total_scheduled = sum(len(scheduled.get(w, []) or []) for w in workers)
        redis_list_len = None
        redis_error = None
        try:
            conn = client.broker_connection().default_channel.client
            redis_list_len = int(conn.llen("celery"))
        except Exception as exc:  # pragma: no cover - runtime dependent
            redis_error = str(exc)
        return {
            "celery_unavailable": False,
            "workers": len(workers),
            "active": total_active,
            "reserved": total_reserved,
            "scheduled": total_scheduled,
            "redis_list_len": redis_list_len,
            "redis_error": redis_error,
        }
    except Exception as exc:  # pragma: no cover - runtime dependent
        return {
            "celery_unavailable": True,
            "workers": 0,
            "active": 0,
            "reserved": 0,
            "scheduled": 0,
            "redis_list_len": None,
            "redis_error": str(exc),
        }


def _job_scope_sql(
    namespace: str | None,
    project_key: str | None,
) -> tuple[str, list[Any]]:
    """Build ``WHERE`` suffix for ``jobs`` / ``j`` alias, and bound args. Empty when unfiltered."""
    from utils.luma_brain import _DEFAULT_JOB_NAMESPACE, _DEFAULT_PROJECT_KEY, _coalesce_job_scope

    parts: list[str] = []
    args: list[Any] = []
    if namespace is not None:
        parts.append("namespace = ?")
        args.append(_coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE))
    if project_key is not None:
        parts.append("project_key = ?")
        args.append(_coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY))
    if not parts:
        return "", []
    return "WHERE " + " AND ".join(parts), args


def _scope_conditions(
    namespace: str | None,
    project_key: str | None,
) -> tuple[list[str], list[Any]]:
    """Bare ``jobs`` scope predicates (no ``WHERE``), composable with other conditions."""
    from utils.luma_brain import _DEFAULT_JOB_NAMESPACE, _DEFAULT_PROJECT_KEY, _coalesce_job_scope

    parts: list[str] = []
    args: list[Any] = []
    if namespace is not None:
        parts.append("namespace = ?")
        args.append(_coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE))
    if project_key is not None:
        parts.append("project_key = ?")
        args.append(_coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY))
    return parts, args


def collect_latency_percentiles(
    conn: Any,
    *,
    namespace: str | None,
    project_key: str | None,
    window_sec: int = 21600,
    max_rows: int = 5000,
) -> dict[str, Any]:
    """Real p50/p95/p99 over recent ``jobs`` rows (SQLite SSOT; computed in Python)."""
    parts, scope_args = _scope_conditions(namespace, project_key)
    now = int(time.time())
    cutoff = now - int(window_sec)

    lat_where = " AND ".join(["total_latency_ms IS NOT NULL", "updated_at >= ?", *parts])
    lat_rows = conn.execute(
        f"SELECT total_latency_ms FROM jobs WHERE {lat_where} ORDER BY updated_at DESC LIMIT ?",
        (cutoff, *scope_args, int(max_rows)),
    ).fetchall()
    lat = [int(r[0]) for r in lat_rows if r[0] is not None]

    qw_where = " AND ".join(["queue_wait_ms IS NOT NULL", "updated_at >= ?", *parts])
    qw_rows = conn.execute(
        f"SELECT queue_wait_ms FROM jobs WHERE {qw_where} ORDER BY updated_at DESC LIMIT ?",
        (cutoff, *scope_args, int(max_rows)),
    ).fetchall()
    qwait = [int(r[0]) for r in qw_rows if r[0] is not None]

    return {
        "window_sec": int(window_sec),
        "sample_count": len(lat),
        "total_latency_ms": {
            "p50": percentile_nearest_rank(lat, 0.50),
            "p95": percentile_nearest_rank(lat, 0.95),
            "p99": percentile_nearest_rank(lat, 0.99),
            "max": max(lat) if lat else None,
        },
        "queue_wait_ms": {
            "p50": percentile_nearest_rank(qwait, 0.50),
            "p95": percentile_nearest_rank(qwait, 0.95),
        },
        "source": "jobs.total_latency_ms (sqlite_ssot)",
    }


def collect_slo_window(
    conn: Any,
    *,
    namespace: str | None,
    project_key: str | None,
    window_sec: int = 3600,
) -> dict[str, Any]:
    """Success-rate SLI + error-budget over a recent window (terminal ``jobs`` only)."""
    import os

    try:
        target_pct = float(os.environ.get("LIVEHOUSE_SLO_TARGET_PCT", "99.0"))
    except ValueError:
        target_pct = 99.0
    target_pct = min(100.0, max(0.0, target_pct))

    parts, scope_args = _scope_conditions(namespace, project_key)
    now = int(time.time())
    cutoff = now - int(window_sec)
    where = " AND ".join(["updated_at >= ?", *parts])
    row = conn.execute(
        f"""
        SELECT
          SUM(CASE WHEN status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS ok,
          SUM(CASE WHEN status IN ('FAILED_RETRYABLE','FAILED_PERMANENT','DEAD_LETTERED','CANCELLED')
                   THEN 1 ELSE 0 END) AS bad
        FROM jobs WHERE {where}
        """,
        (cutoff, *scope_args),
    ).fetchone()
    ok = int((row["ok"] if row else 0) or 0)
    bad = int((row["bad"] if row else 0) or 0)
    completed = ok + bad
    success_rate = (ok / completed * 100.0) if completed else None

    budget_remaining: float | None = None
    if completed and success_rate is not None:
        allowed = (100.0 - target_pct) / 100.0
        if allowed > 0:
            budget_remaining = max(0.0, (1.0 - (bad / completed) / allowed)) * 100.0
        elif bad == 0:
            budget_remaining = 100.0
        else:
            budget_remaining = 0.0

    return {
        "window_sec": int(window_sec),
        "target_pct": target_pct,
        "completed": completed,
        "succeeded": ok,
        "failed": bad,
        "success_rate_pct": round(success_rate, 3) if success_rate is not None else None,
        "error_budget_remaining_pct": round(budget_remaining, 1) if budget_remaining is not None else None,
    }


def _metrics_authority_documentation() -> dict[str, Any]:
    """Static map: which sections are authoritative vs best-effort vs this-process only."""
    return {
        "labels": {
            "jobs": "sqlite_ssot",
            "workers": "sqlite_ssot",
            "model_runs": "sqlite_ssot",
            "inference_from_database": "sqlite_ssot_aggregated",
            "stage3_fast_first_routing": "process_local_only",
            "providers": "process_local_only",
            "inference_latency": "process_local_only",
            "inference_queue": "process_local_only",
            "queue_backlog": "broker_best_effort",
            "runtime_snapshots": "worker_published_best_effort",
            "prometheus_text": "process_local_when_scraped",
        },
        "unreliable_without_context": [
            "providers",
            "inference_latency",
            "inference_queue",
            "stage3_fast_first_routing",
            "prometheus exporter values derived from this process",
        ],
        "aggregation_model": (
            "Cross-worker inference counters in JSON come from model_runs / model_run_attempts. "
            "Optional infra_runtime_snapshots merges per-process queue depth when workers persist."
        ),
    }


def collect_inference_aggregates_from_db(
    conn: Any,
    *,
    namespace: str | None,
    project_key: str | None,
) -> dict[str, Any]:
    """Historical / in-flight inference metrics aggregated from the ledger (all workers)."""
    from utils.luma_brain import _jobs_scope_sql_fragment

    scope_frag, scope_args = _jobs_scope_sql_fragment(namespace, project_key, table_alias="j")
    now = int(time.time())
    cutoff_24h = now - 86400

    inflight_row = conn.execute(
        f"""
        SELECT COUNT(*) AS c
        FROM model_runs mr
        JOIN jobs j ON j.id = mr.job_id
        WHERE mr.status IN ('QUEUED', 'STARTED'){scope_frag}
        """,
        scope_args,
    ).fetchone()
    inflight_total = int(inflight_row["c"] if inflight_row else 0)

    term_rows = conn.execute(
        f"""
        SELECT
          COALESCE(
            NULLIF(TRIM(mr.primary_provider), ''),
            NULLIF(TRIM(mr.provider), ''),
            '(unknown)'
          ) AS prov,
          COUNT(*) AS terminal_total,
          SUM(CASE WHEN mr.created_at >= ? THEN 1 ELSE 0 END) AS terminal_24h,
          SUM(CASE WHEN mr.status = 'SUCCEEDED' THEN 1 ELSE 0 END) AS succeeded_total,
          SUM(CASE WHEN mr.status = 'SUCCEEDED' AND mr.created_at >= ? THEN 1 ELSE 0 END) AS succeeded_24h,
          SUM(CASE WHEN mr.status IN ('FAILED', 'TIMEOUT') THEN 1 ELSE 0 END) AS failed_total,
          SUM(
            CASE WHEN mr.status IN ('FAILED', 'TIMEOUT') AND mr.created_at >= ? THEN 1 ELSE 0 END
          ) AS failed_24h,
          SUM(CASE WHEN IFNULL(mr.fallback_used, 0) != 0 THEN 1 ELSE 0 END) AS fallback_total,
          AVG(CASE WHEN mr.end_to_end_latency_ms IS NOT NULL THEN mr.end_to_end_latency_ms END)
            AS avg_e2e_ms_all,
          AVG(CASE WHEN mr.created_at >= ? AND mr.end_to_end_latency_ms IS NOT NULL
                THEN mr.end_to_end_latency_ms END) AS avg_e2e_ms_24h,
          AVG(CASE WHEN mr.provider_latency_ms IS NOT NULL THEN mr.provider_latency_ms END)
            AS avg_provider_ms_all,
          AVG(CASE WHEN mr.created_at >= ? AND mr.provider_latency_ms IS NOT NULL
                THEN mr.provider_latency_ms END) AS avg_provider_ms_24h,
          AVG(CASE WHEN mr.queue_wait_ms IS NOT NULL THEN mr.queue_wait_ms END) AS avg_queue_wait_ms_all
        FROM model_runs mr
        JOIN jobs j ON j.id = mr.job_id
        WHERE mr.status IN ('SUCCEEDED', 'FAILED', 'TIMEOUT', 'CANCELLED'){scope_frag}
        GROUP BY prov
        ORDER BY prov ASC
        """,
        (
            cutoff_24h,
            cutoff_24h,
            cutoff_24h,
            cutoff_24h,
            cutoff_24h,
            *scope_args,
        ),
    ).fetchall()
    by_provider: list[dict[str, Any]] = []
    for r in term_rows:
        avg_e2e = r["avg_e2e_ms_all"]
        avg_e2e_24 = r["avg_e2e_ms_24h"]
        avg_pr = r["avg_provider_ms_all"]
        avg_pr_24 = r["avg_provider_ms_24h"]
        avg_qw = r["avg_queue_wait_ms_all"]
        by_provider.append(
            {
                "provider": str(r["prov"]),
                "terminal_total": int(r["terminal_total"] or 0),
                "terminal_last_24h": int(r["terminal_24h"] or 0),
                "succeeded_total": int(r["succeeded_total"] or 0),
                "succeeded_last_24h": int(r["succeeded_24h"] or 0),
                "failed_total": int(r["failed_total"] or 0),
                "failed_last_24h": int(r["failed_24h"] or 0),
                "fallback_marked_total": int(r["fallback_total"] or 0),
                "avg_e2e_latency_ms": int(round(avg_e2e)) if avg_e2e is not None else None,
                "avg_e2e_latency_ms_last_24h": int(round(avg_e2e_24)) if avg_e2e_24 is not None else None,
                "avg_provider_latency_ms": int(round(avg_pr)) if avg_pr is not None else None,
                "avg_provider_latency_ms_last_24h": int(round(avg_pr_24)) if avg_pr_24 is not None else None,
                "avg_queue_wait_ms": int(round(avg_qw)) if avg_qw is not None else None,
            }
        )

    att_rows = conn.execute(
        f"""
        SELECT
          mra.provider_id AS prov,
          COUNT(*) AS attempts,
          SUM(CASE WHEN mra.ok != 0 THEN 1 ELSE 0 END) AS attempts_ok,
          SUM(CASE WHEN mra.ok = 0 THEN 1 ELSE 0 END) AS attempts_fail
        FROM model_run_attempts mra
        JOIN model_runs mr ON mr.id = mra.model_run_id
        JOIN jobs j ON j.id = mr.job_id
        WHERE 1 = 1{scope_frag}
        GROUP BY mra.provider_id
        ORDER BY mra.provider_id ASC
        """,
        scope_args,
    ).fetchall()
    attempts_by_provider = [
        {
            "provider": str(r["prov"]),
            "attempts": int(r["attempts"] or 0),
            "ok": int(r["attempts_ok"] or 0),
            "fail": int(r["attempts_fail"] or 0),
        }
        for r in att_rows
    ]

    return {
        "scope": {
            "namespace": namespace,
            "project_key": project_key,
        },
        "model_runs_inflight_in_db": inflight_total,
        "terminal_by_provider": by_provider,
        "attempts_by_provider": attempts_by_provider,
        "windows": {"last_24h_since_unix": cutoff_24h, "as_of_unix": now},
    }


def _runtime_snapshots_summary(conn: Any, *, now: int) -> dict[str, Any]:
    from utils.luma_brain import list_infra_runtime_snapshots

    rows = list_infra_runtime_snapshots(conn)
    inference_instances: list[dict[str, Any]] = []
    stale_sec = 120
    for row in rows:
        if row.get("component") != "inference_queue":
            continue
        pay = row.get("payload") or {}
        if not isinstance(pay, dict):
            pay = {}
        ts = int(row.get("updated_at") or 0)
        inference_instances.append(
            {
                "source": row.get("source"),
                "updated_at": ts,
                "age_sec": max(0, now - ts) if ts else None,
                "stale": (now - ts) > stale_sec if ts else True,
                "depth": pay.get("depth"),
                "active_workers": pay.get("active_workers"),
                "max_inflight": pay.get("max_inflight"),
            }
        )
    total_depth = sum(int(i.get("depth") or 0) for i in inference_instances)
    total_active = sum(int(i.get("active_workers") or 0) for i in inference_instances)
    return {
        "inference_queue": {
            "instances": inference_instances,
            "totals": {"sum_depth": total_depth, "sum_active_workers": total_active, "instances": len(inference_instances)},
            "stale_threshold_sec": stale_sec,
            "note": "Populated when workers run PrioritizedInferenceQueue with DB writes enabled.",
        },
        "raw_row_count": len(rows),
    }


def collect_infra_metrics(
    conn: Any,
    *,
    namespace: str | None = None,
    project_key: str | None = None,
) -> dict[str, Any]:
    """
    Job counts may be filtered by ``namespace`` and/or ``project_key`` (optional).
    When unfiltered, ``jobs.by_namespace`` and ``jobs.by_namespace_project`` add platform-style breakdowns.
    """
    from utils.luma_brain import _DEFAULT_JOB_NAMESPACE, _DEFAULT_PROJECT_KEY, _coalesce_job_scope, mark_stale_workers_offline

    mark_stale_workers_offline(conn)

    scope_where, scope_args = _job_scope_sql(namespace, project_key)
    filtered = bool(scope_args)
    jobs_sql = f"SELECT status, COUNT(*) AS c FROM jobs {scope_where} GROUP BY status ORDER BY status ASC"
    by_status_rows = conn.execute(jobs_sql, scope_args).fetchall()
    jobs_by_status = {str(r["status"]): int(r["c"]) for r in by_status_rows}
    workers_total = int(conn.execute("SELECT COUNT(*) AS c FROM workers").fetchone()["c"])
    wstatus_rows = conn.execute(
        "SELECT status, COUNT(*) AS c FROM workers GROUP BY status ORDER BY status ASC"
    ).fetchall()
    workers_by_status = {str(r["status"]): int(r["c"]) for r in wstatus_rows}
    workers_by_executor_rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(worker_type), ''), '(blank)') AS executor_pool, COUNT(*) AS c
            FROM workers
            GROUP BY COALESCE(NULLIF(TRIM(worker_type), ''), '(blank)')
            ORDER BY executor_pool ASC
            """
        ).fetchall()
    ]
    from utils.luma_brain import cluster_headroom_for_dispatch

    now = int(time.time())
    dispatch_cluster = cluster_headroom_for_dispatch(conn)
    executor_snap = dispatch_cluster["executor_pools"]
    pipeline_admission = {
        "headroom": dispatch_cluster["headroom"],
        "total_capacity": dispatch_cluster["total_capacity"],
        "total_inflight": dispatch_cluster["total_inflight"],
        "online_workers": dispatch_cluster["online_workers"],
        "total_worker_rows": dispatch_cluster["total_worker_rows"],
    }

    fresh_window = 120
    fresh_workers = int(
        conn.execute(
            "SELECT COUNT(*) AS c FROM workers WHERE last_heartbeat IS NOT NULL AND last_heartbeat >= ?",
            (now - fresh_window,),
        ).fetchone()["c"]
    )
    j_parts: list[str] = []
    j_args: list[Any] = []
    if namespace is not None:
        j_parts.append("j.namespace = ?")
        j_args.append(_coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE))
    if project_key is not None:
        j_parts.append("j.project_key = ?")
        j_args.append(_coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY))
    if filtered:
        mr_join = "FROM model_runs mr JOIN jobs j ON j.id = mr.job_id WHERE " + " AND ".join(j_parts)
        model_runs_total = int(conn.execute(f"SELECT COUNT(*) AS c {mr_join}", j_args).fetchone()["c"])
        mr_err_rows = conn.execute(
            f"""
            SELECT COALESCE(mr.error_type, '') AS et, COUNT(*) AS c
            {mr_join}
              AND mr.error_type IS NOT NULL AND TRIM(mr.error_type) != ''
            GROUP BY mr.error_type
            """,
            j_args,
        ).fetchall()
    else:
        model_runs_total = int(conn.execute("SELECT COUNT(*) AS c FROM model_runs").fetchone()["c"])
        mr_err_rows = conn.execute(
            """
            SELECT COALESCE(error_type, '') AS et, COUNT(*) AS c
            FROM model_runs
            WHERE error_type IS NOT NULL AND TRIM(error_type) != ''
            GROUP BY error_type
            """
        ).fetchall()
    model_runs_by_error_type = {str(r["et"]): int(r["c"]) for r in mr_err_rows}

    by_ns: dict[str, int] = {}
    by_nsp: list[dict[str, Any]] = []
    if not filtered:
        for r in conn.execute(
            "SELECT namespace, COUNT(*) AS c FROM jobs GROUP BY namespace ORDER BY namespace ASC"
        ).fetchall():
            by_ns[str(r["namespace"])] = int(r["c"])
        triple = conn.execute(
            """
            SELECT namespace, project_key, status, COUNT(*) AS c
            FROM jobs
            GROUP BY namespace, project_key, status
            """
        ).fetchall()
        acc: dict[tuple[str, str], dict[str, Any]] = {}
        for r in triple:
            ns, pk = str(r["namespace"]), str(r["project_key"])
            st, c = str(r["status"]), int(r["c"])
            key = (ns, pk)
            if key not in acc:
                acc[key] = {
                    "namespace": ns,
                    "project_key": pk,
                    "total": 0,
                    "by_status": {},
                }
            acc[key]["total"] += c
            acc[key]["by_status"][st] = c
        by_nsp = sorted(acc.values(), key=lambda x: (x["namespace"], x["project_key"]))
        inflight_nsp_rows = conn.execute(
            """
            SELECT namespace, project_key, COUNT(*) AS c
            FROM jobs
            WHERE status IN ('CLAIMED', 'PREPROCESSING', 'INFERENCING', 'POSTPROCESSING')
            GROUP BY namespace, project_key
            ORDER BY namespace ASC, project_key ASC
            """
        ).fetchall()
        inflight_by_namespace_project = [
            {
                "namespace": str(r["namespace"]),
                "project_key": str(r["project_key"]),
                "active_jobs": int(r["c"]),
            }
            for r in inflight_nsp_rows
        ]
        mr_nsp_rows = conn.execute(
            """
            SELECT j.namespace, j.project_key, COUNT(*) AS c
            FROM model_runs mr
            JOIN jobs j ON j.id = mr.job_id
            GROUP BY j.namespace, j.project_key
            ORDER BY j.namespace ASC, j.project_key ASC
            """
        ).fetchall()
        model_runs_by_namespace_project = [
            {
                "namespace": str(r["namespace"]),
                "project_key": str(r["project_key"]),
                "total": int(r["c"]),
            }
            for r in mr_nsp_rows
        ]

    inference_db = collect_inference_aggregates_from_db(conn, namespace=namespace, project_key=project_key)
    rt_snap = _runtime_snapshots_summary(conn, now=now)
    latency_pct = collect_latency_percentiles(conn, namespace=namespace, project_key=project_key)
    slo_window = collect_slo_window(conn, namespace=namespace, project_key=project_key)

    queue = queue_backlog_snapshot()
    providers = provider_runtime_metrics()
    inference_queue = inference_queue_runtime_snapshot()
    try:
        from infra.scope_quota import scope_quota_snapshot

        scope_quota = scope_quota_snapshot(namespace=namespace, project_key=project_key)
    except Exception as exc:
        scope_quota = {"enforced": False, "error": str(exc)[:200]}
    try:
        from infra.otel_bootstrap import last_otel_bootstrap_status

        otel_status = last_otel_bootstrap_status()
    except Exception as exc:
        otel_status = {"configured": False, "error": str(exc)[:200]}
    try:
        from utils.brain_backend import get_brain_backend, normalize_brain_backend_name
        import os as _os

        brain_backend = {
            "selected": normalize_brain_backend_name(_os.environ.get("LIVEHOUSE_BRAIN_BACKEND")),
            "dialect": get_brain_backend().dialect(),
            "authority": "env_LIVEHOUSE_BRAIN_BACKEND",
        }
    except Exception as exc:
        brain_backend = {"selected": "sqlite", "error": str(exc)[:200]}
    if _PROM_AVAILABLE:
        if not filtered:
            for s, c in jobs_by_status.items():
                _P_JOBS_STATUS.labels(status=s).set(c)
        _P_QUEUE_BACKLOG.labels(kind="active").set(int(queue.get("active") or 0))
        _P_QUEUE_BACKLOG.labels(kind="reserved").set(int(queue.get("reserved") or 0))
        _P_QUEUE_BACKLOG.labels(kind="scheduled").set(int(queue.get("scheduled") or 0))
        _P_QUEUE_BACKLOG.labels(kind="redis_list_len").set(int(queue.get("redis_list_len") or 0))
        _P_WORKERS.labels(kind="total").set(workers_total)
        _P_WORKERS.labels(kind="fresh_within_120s").set(fresh_workers)
        for st, c in workers_by_status.items():
            _P_WORKERS.labels(kind=f"status:{st}").set(int(c))
    jobs_out: dict[str, Any] = {
        "total": int(sum(jobs_by_status.values())),
        "by_status": jobs_by_status,
    }
    if namespace is not None or project_key is not None:
        jobs_out["filter"] = {
            "namespace": _coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE)
            if namespace is not None
            else None,
            "project_key": _coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY)
            if project_key is not None
            else None,
        }
    if not filtered:
        jobs_out["by_namespace"] = by_ns
        jobs_out["by_namespace_project"] = by_nsp
        jobs_out["inflight_by_namespace_project"] = inflight_by_namespace_project
    model_runs_out: dict[str, Any] = {
        "total": model_runs_total,
        "by_error_type": model_runs_by_error_type,
    }
    if namespace is not None or project_key is not None:
        model_runs_out["filter"] = {
            "namespace": _coalesce_job_scope(namespace, default=_DEFAULT_JOB_NAMESPACE)
            if namespace is not None
            else None,
            "project_key": _coalesce_job_scope(project_key, default=_DEFAULT_PROJECT_KEY)
            if project_key is not None
            else None,
        }
    if not filtered:
        model_runs_out["by_namespace_project"] = model_runs_by_namespace_project
    return {
        "jobs": jobs_out,
        "queue_backlog": queue,
        "workers": {
            "total": workers_total,
            "fresh_within_120s": fresh_workers,
            "heartbeat_fresh_window_sec": fresh_window,
            "by_status": workers_by_status,
            "executor_pools": executor_snap,
            "pipeline_admission": pipeline_admission,
            "workers_by_executor_group": workers_by_executor_rows,
        },
        "model_runs": model_runs_out,
        "providers": providers["providers"],
        "inference_latency": {
            "avg_ms": providers["avg_latency_ms"],
            "last_ms": providers["last_latency_ms"],
        },
        "latency": latency_pct,
        "slo": slo_window,
        "inference_queue": inference_queue,
        "scope_vlm_quota": scope_quota,
        "otel": otel_status,
        "brain_backend": brain_backend,
        "metrics_authority": _metrics_authority_documentation(),
        "inference_from_database": inference_db,
        "runtime_snapshots": rt_snap,
        "stage3_fast_first_routing": stage3_fast_first_counters_snapshot(),
    }
