"""
Minimal orchestration-style dispatch policy for ``tasks.run_job``.

Celery remains a transport; SSOT is ``jobs``. This module decides *how many* and *which*
job ids to hand to ``send_task`` in a given round (ingest, sweeps, etc.) using:

- a global per-round cap
- per-``job_type`` caps
- cluster headroom (sum of worker ``capacity - live pipeline-active jobs`` for ``ONLINE`` workers;
  ``jobs`` rows are authoritative — not ``workers.inflight``) and
  full admission stop when workers exist in SSOT but none are ``ONLINE`` (e.g. all ``PAUSED``/``DRAINING``)
- weighted round-robin across job types to avoid one type monopolizing a batch
- job row ``priority`` (higher first), then ``enqueued_at``, then ``id`` within each type
- optional provider / runtime signals (SQLite job + model_runs pressure, plus in-process
  ``infra.metrics`` when available) to cap per-provider dispatch share; blended **pressure**
  from failure rate, inflight/base, and latency (with EMA smoothing) yields
  ``effective_slots ≈ base * (1 - throttle_strength * pressure)``
- optional **dispatch scope** via ``LIVEHOUSE_DISPATCH_*`` env vars so one worker fleet can
  prioritize jobs for one ``namespace`` / ``project_key`` without separate brokers (see ``docs/PLATFORM_SCOPE.txt``)
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import asdict, dataclass, field
from typing import Any

from utils.luma_brain import (
    cluster_headroom_for_dispatch,
    dispatch_scope_from_env,
    gather_provider_dispatch_signals,
    get_jobs_dispatch_metadata,
)

from .stage3_scheduler import Stage3Scheduler

logger = logging.getLogger(__name__)

# Smoothed pressure per normalized provider key (process-local; dampens flip-flopping caps).
_LAST_PROVIDER_PRESSURE_EMA: dict[str, float] = {}

# Precedence for interleaving types when both appear in a batch (oldest / ops-friendly first).
_TYPE_DISPATCH_ORDER: tuple[str, ...] = (
    "ANALYZE_PATH",
    "ANALYZE_SESSION",
    "PIPELINE_STAGE",
)


def _parse_provider_caps_env(raw: str | None) -> dict[str, int]:
    """``name=limit,name2=limit`` → limits per normalized provider key."""
    if raw is None or not str(raw).strip():
        return {}
    out: dict[str, int] = {}
    for part in str(raw).split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        ks = k.strip()
        if not ks:
            continue
        try:
            out[ks] = max(0, int(v.strip()))
        except ValueError:
            continue
    return out


def _normalize_provider_key(row: dict[str, Any]) -> str:
    p = row.get("provider")
    if p is None:
        return "_default"
    s = str(p).strip()
    return s if s else "_default"


def _merge_process_metrics(signals: dict[str, Any]) -> dict[str, Any]:
    out = dict(signals)
    try:
        from infra import metrics as infra_metrics

        snap = infra_metrics.provider_runtime_metrics()
        out["process_providers"] = list(snap.get("providers") or [])
        out["process_avg_latency_ms"] = snap.get("avg_latency_ms")
    except Exception:
        out["process_providers"] = []
        out["process_avg_latency_ms"] = None
    return out


def _failure_ratio_process(process_rows: list[dict[str, Any]], pk: str) -> float | None:
    for row in process_rows:
        if str(row.get("provider") or "") != pk:
            continue
        req = int(row.get("requests") or 0)
        if req < 5:
            return None
        fail = int(row.get("failures") or 0)
        fb = int(row.get("fallbacks") or 0)
        fr = fail / max(1, req)
        fbr = fb / max(1, req)
        return min(1.0, fr + 0.25 * fbr)
    return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _latency_pressure_ms(
    *,
    avg_ms: float | None,
    soft_limit: int,
    span: int,
) -> float:
    """Maps observed average latency to [0,1]: 0 at/under soft limit, ramps to 1 over ``span`` ms."""
    if avg_ms is None or soft_limit <= 0:
        return 0.0
    am = float(avg_ms)
    if am <= float(soft_limit):
        return 0.0
    span = max(1, int(span))
    return _clamp01((am - float(soft_limit)) / float(span))


def _process_avg_latency_ms_for_provider(
    process_rows: list[dict[str, Any]], pk: str
) -> float | None:
    for row in process_rows:
        if str(row.get("provider") or "") != pk:
            continue
        v = row.get("avg_latency_ms")
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


def _normalize_pressure_weights(
    wf: float, wi: float, wl: float
) -> tuple[float, float, float]:
    """Blend weights for failure / inflight / latency; normalized to sum to 1."""
    a = max(0.0, float(wf))
    b = max(0.0, float(wi))
    c = max(0.0, float(wl))
    s = a + b + c
    if s <= 0.0:
        return (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0)
    return (a / s, b / s, c / s)


def _effective_caps_per_provider(
    policy: "DispatchPolicy",
    *,
    sqlite_signals: dict[str, Any],
    provider_keys: set[str],
    effective_global_max: int,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Returns caps keyed like ``_normalize_provider_key`` (trimmed literal or ``_default``)."""
    merged = _merge_process_metrics(sqlite_signals)
    inflight = dict(sqlite_signals.get("inflight_by_provider") or {})
    finished = dict(sqlite_signals.get("finished_stats_by_provider") or {})
    proc_rows = list(merged.get("process_providers") or [])
    caps: dict[str, int] = {}
    explain: dict[str, Any] = {}
    eg = max(0, int(effective_global_max))

    wf, wi, wl = _normalize_pressure_weights(
        policy.pressure_weight_failure,
        policy.pressure_weight_inflight,
        policy.pressure_weight_latency,
    )

    # Drop stale EMA entries so the dict does not grow forever with renamed providers.
    for stale in set(_LAST_PROVIDER_PRESSURE_EMA.keys()) - provider_keys:
        del _LAST_PROVIDER_PRESSURE_EMA[stale]

    alpha = _clamp01(policy.pressure_ema_alpha)

    for pk in sorted(provider_keys):
        base = int(policy.per_provider_max.get(pk, policy.default_per_provider_max))
        base = max(0, min(base, eg))
        if base <= 0:
            caps[pk] = 0
            explain[pk] = {"base_cap": 0, "pressure": 0.0, "effective_slots": 0}
            continue

        st = dict(finished.get(pk) or {})
        succeeded = int(st.get("succeeded") or 0)
        failed = int(st.get("failed_terminal") or 0)
        completed = succeeded + failed
        sqlite_fail_r = (failed / max(1, completed)) if completed > 0 else 0.0
        proc_fr = _failure_ratio_process(proc_rows, pk)
        failure_ratio = max(sqlite_fail_r, proc_fr) if proc_fr is not None else sqlite_fail_r
        failure_pressure = _clamp01(failure_ratio)

        inf = int(inflight.get(pk, 0))
        denom = max(1, base)
        inflight_pressure = _clamp01(float(inf) / float(denom))

        sqlite_ai = st.get("avg_inference_ms")
        sqlite_at = st.get("avg_total_latency_ms")
        lat_candidates: list[float] = []
        if sqlite_ai is not None:
            lat_candidates.append(float(sqlite_ai))
        if sqlite_at is not None:
            lat_candidates.append(float(sqlite_at))
        proc_lat = _process_avg_latency_ms_for_provider(proc_rows, pk)
        if proc_lat is not None:
            lat_candidates.append(proc_lat)
        merged_avg_ms = max(lat_candidates) if lat_candidates else None

        lat_p = _latency_pressure_ms(
            avg_ms=merged_avg_ms,
            soft_limit=policy.latency_soft_limit_ms,
            span=policy.latency_pressure_span_ms,
        )

        # Weighted sum (not max) avoids a single noisy signal snapping caps; raw and components stay in [0, 1].
        pressure_raw = _clamp01(
            wf * failure_pressure + wi * inflight_pressure + wl * lat_p
        )

        # EMA damps rapid oscillation when SQLite windows or inflight fluctuate round-to-round.
        prev = _LAST_PROVIDER_PRESSURE_EMA.get(pk, pressure_raw)
        pressure = _clamp01(alpha * pressure_raw + (1.0 - alpha) * prev)
        _LAST_PROVIDER_PRESSURE_EMA[pk] = pressure

        # Slots scale linearly with headroom: more pressure ⇒ fewer dispatches.
        raw_slots = float(base) * (1.0 - _clamp01(policy.provider_throttle_strength * pressure))
        slots = int(math.floor(raw_slots))
        slots = max(policy.per_provider_min_slots, slots)
        slots = max(0, min(slots, base, eg))
        caps[pk] = slots

        explain[pk] = {
            "base_cap": base,
            "failure_ratio_sqlite": round(sqlite_fail_r, 4),
            "failure_ratio_process": None if proc_fr is None else round(proc_fr, 4),
            "failure_pressure": round(failure_pressure, 4),
            "inflight_jobs": inf,
            "inflight_pressure": round(inflight_pressure, 4),
            "avg_latency_ms": None if merged_avg_ms is None else round(merged_avg_ms, 2),
            "latency_pressure": round(lat_p, 4),
            "pressure_raw": round(pressure_raw, 4),
            "pressure": round(pressure, 4),
            "effective_slots": slots,
            "effective_slots_float": round(raw_slots, 4),
            "combined_pressure": round(pressure, 4),
            "effective_cap": slots,
        }

        logger.info(
            "dispatch provider throttle provider=%s avg_latency_ms=%s inflight=%s failure_rate=%s "
            "pressure=%s effective_slots=%s base_cap=%s",
            pk,
            explain[pk]["avg_latency_ms"],
            inf,
            round(failure_pressure, 4),
            round(pressure, 4),
            slots,
            base,
        )

    return caps, {"providers": explain, "signals_summary": merged}


@dataclass(frozen=True)
class DispatchPolicy:
    """Tunable without code changes via env vars (see ``from_env``)."""

    name: str = "v2_runtime_fair_batch"
    max_per_round: int = 32
    """Hard cap on run_job dispatches in one policy invocation."""
    default_per_type_max: int = 12
    """Max jobs of an unknown type in one round."""
    per_type_max: dict[str, int] = field(
        default_factory=lambda: {
            "ANALYZE_SESSION": 16,
            "ANALYZE_PATH": 8,
            "PIPELINE_STAGE": 8,
        }
    )
    respect_headroom: bool = True
    """If True, effective batch size is also bounded by ``cluster_headroom``."""

    use_provider_runtime_signals: bool = True
    """When True, merge SQLite + optional process metrics for per-provider caps."""
    default_per_provider_max: int = 32
    """Ceiling per provider per round before throttle (unless overridden)."""
    per_provider_max: dict[str, int] = field(default_factory=dict)
    """Optional overrides keyed by normalized ``jobs.provider`` string."""
    provider_throttle_strength: float = 1.0
    """Scales blended ``pressure``: ``effective_slots ≈ base * (1 - strength * pressure)``."""
    # Normalized blend of failure / inflight / latency pressures (each in [0, 1]); see ``_normalize_pressure_weights``.
    pressure_weight_failure: float = 1.0 / 3.0
    pressure_weight_inflight: float = 1.0 / 3.0
    pressure_weight_latency: float = 1.0 / 3.0
    pressure_ema_alpha: float = 0.35
    """EMA smoothing for ``pressure`` (higher = react faster; lower = less oscillation)."""
    inflight_signal_weight: float = 0.45
    """Legacy field (unused by cap logic); retained for compatibility."""
    latency_soft_limit_ms: int = 120_000
    """Average latency above this (SQLite / process window) adds pressure toward 1."""
    latency_pressure_span_ms: int = 60_000
    latency_signal_weight: float = 0.35
    """Legacy field (unused by cap logic); retained for compatibility."""
    finished_stats_window_seconds: int = 3600
    """Rolling window for SQLite terminal-job stats."""
    per_provider_min_slots: int = 1
    """Floor after throttle (set to 0 to allow starving a provider completely)."""

    @classmethod
    def from_env(cls) -> "DispatchPolicy":
        def _i(key: str, default: int) -> int:
            raw = os.environ.get(key)
            if raw is None or raw == "":
                return default
            return max(0, int(raw))

        def _f(key: str, default: float) -> float:
            raw = os.environ.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        caps = _parse_provider_caps_env(os.environ.get("LIVEHOUSE_DISPATCH_PROVIDER_CAPS"))
        per_prov_default = _i("LIVEHOUSE_DISPATCH_PER_PROVIDER_MAX", 32)
        pwf = max(0.0, _f("LIVEHOUSE_DISPATCH_PRESSURE_WEIGHT_FAILURE", 1.0 / 3.0))
        pwi = max(0.0, _f("LIVEHOUSE_DISPATCH_PRESSURE_WEIGHT_INFLIGHT", 1.0 / 3.0))
        pwl = max(0.0, _f("LIVEHOUSE_DISPATCH_PRESSURE_WEIGHT_LATENCY", 1.0 / 3.0))

        return cls(
            max_per_round=_i("LIVEHOUSE_DISPATCH_MAX_PER_ROUND", 32),
            default_per_type_max=_i("LIVEHOUSE_DISPATCH_PER_TYPE_MAX", 12),
            per_type_max={
                "ANALYZE_SESSION": _i("LIVEHOUSE_DISPATCH_MAX_ANALYZE_SESSION", 16),
                "ANALYZE_PATH": _i("LIVEHOUSE_DISPATCH_MAX_ANALYZE_PATH", 8),
                "PIPELINE_STAGE": _i("LIVEHOUSE_DISPATCH_MAX_PIPELINE_STAGE", 8),
            },
            respect_headroom=_i("LIVEHOUSE_DISPATCH_RESPECT_HEADROOM", 1) != 0,
            use_provider_runtime_signals=_i("LIVEHOUSE_DISPATCH_RUNTIME_SIGNALS", 1) != 0,
            default_per_provider_max=per_prov_default,
            per_provider_max=caps,
            provider_throttle_strength=max(0.0, min(1.0, _f("LIVEHOUSE_DISPATCH_THROTTLE_STRENGTH", 1.0))),
            pressure_weight_failure=pwf,
            pressure_weight_inflight=pwi,
            pressure_weight_latency=pwl,
            pressure_ema_alpha=max(0.0, min(1.0, _f("LIVEHOUSE_DISPATCH_PRESSURE_EMA_ALPHA", 0.35))),
            inflight_signal_weight=max(0.0, min(1.0, _f("LIVEHOUSE_DISPATCH_INFLIGHT_WEIGHT", 0.45))),
            latency_soft_limit_ms=_i("LIVEHOUSE_DISPATCH_LATENCY_SOFT_LIMIT_MS", 120_000),
            latency_pressure_span_ms=max(1, _i("LIVEHOUSE_DISPATCH_LATENCY_PRESSURE_SPAN_MS", 60_000)),
            latency_signal_weight=max(0.0, min(1.0, _f("LIVEHOUSE_DISPATCH_LATENCY_WEIGHT", 0.35))),
            finished_stats_window_seconds=max(60, _i("LIVEHOUSE_DISPATCH_FINISHED_WINDOW_SEC", 3600)),
            per_provider_min_slots=_i("LIVEHOUSE_DISPATCH_PROVIDER_MIN_SLOTS", 1),
        )

    def max_for_type(self, job_type: str) -> int:
        return int(self.per_type_max.get(job_type, self.default_per_type_max))


@dataclass
class DispatchPlan:
    """Outcome of :func:`plan_dispatch` — suitable for structured logs and metrics."""

    policy: DispatchPolicy
    headroom: int
    online_workers: int
    total_worker_rows: int
    total_capacity: int
    total_inflight: int
    effective_max: int
    candidate_count: int
    selected_job_ids: list[int]
    by_type_chosen: dict[str, int]
    skipped_by_type: dict[str, int]
    skipped_total_over_cap: int
    note: str | None = None
    provider_effective_caps: dict[str, int] | None = None
    provider_signals: dict[str, Any] | None = None
    dispatch_decisions: dict[str, Any] | None = None
    executor_pools_snapshot: dict[str, Any] | None = None

    def to_log_dict(self) -> dict[str, Any]:
        d = asdict(self)
        p = d.pop("policy")
        d["policy"] = asdict(p) if isinstance(p, DispatchPolicy) else p
        return d


def _type_sort_key(t: str) -> tuple[int, str]:
    try:
        idx = _TYPE_DISPATCH_ORDER.index(t)
    except ValueError:
        idx = len(_TYPE_DISPATCH_ORDER)
    return (idx, t)


def _bucket_candidates(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group by job_type; each bucket sorted: priority desc, enqueued_at asc, id asc."""
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        jt = str(r.get("job_type") or "UNKNOWN")
        buckets.setdefault(jt, []).append(r)
    for jt, lst in buckets.items():
        lst.sort(
            key=lambda x: (
                -int(x.get("priority") or 0),
                int(x.get("enqueued_at") or 0),
                int(x.get("id") or 0),
            )
        )
    return buckets


def _ordered_types(buckets: dict[str, list[dict[str, Any]]]) -> list[str]:
    return sorted(buckets.keys(), key=_type_sort_key)


def select_jobs_weighted_fair(
    rows: list[dict[str, Any]],
    *,
    effective_max: int,
    policy: DispatchPolicy,
    provider_effective_caps: dict[str, int] | None = None,
    executor_pool_remaining: dict[str, int] | None = None,
) -> tuple[list[int], dict[str, int], int, dict[str, Any]]:
    """
    Round-robin across job types. Within each type, priority + FIFO.

    Optional ``provider_effective_caps`` limits picks per normalized ``jobs.provider`` bucket.
    Optional ``executor_pool_remaining`` decrements per ``required_executor_class`` so dispatch does not
    enqueue jobs whose pool has no ONLINE capacity this round.
    """
    meta: dict[str, Any] = {
        "picked_by_provider": {},
        "remaining_by_provider": {},
        "skipped_due_to_provider_cap_est": 0,
        "picked_by_executor_pool": {},
        "skipped_due_to_executor_pool_est": 0,
    }
    if effective_max <= 0 or not rows:
        skip: dict[str, int] = {}
        for r in rows:
            t = str(r.get("job_type") or "UNKNOWN")
            skip[t] = skip.get(t, 0) + 1
        return [], skip, len(rows), meta

    buckets = _bucket_candidates(rows)
    types = _ordered_types(buckets)
    deques: dict[str, list[dict[str, Any]]] = {t: list(buckets[t]) for t in types}
    chosen: list[int] = []
    per_type_picked: dict[str, int] = {t: 0 for t in types}
    skipped_by_type: dict[str, int] = {t: 0 for t in types}
    picked_by_prov: dict[str, int] = {}
    picked_by_pool: dict[str, int] = {}

    use_prov = bool(provider_effective_caps)
    use_pool = bool(executor_pool_remaining)

    while len(chosen) < effective_max:
        progress = False
        for t in types:
            if len(chosen) >= effective_max:
                break
            if per_type_picked[t] >= policy.max_for_type(t):
                continue
            dq = deques[t]
            if not dq:
                continue
            n = len(dq)
            picked_here = False
            for _ in range(n):
                row = dq[0]
                pk = _normalize_provider_key(row)
                req_pool = str(row.get("required_executor_class") or "").strip().lower()

                prov_ok = True
                if use_prov:
                    cap = int(provider_effective_caps.get(pk, effective_max))
                    got = picked_by_prov.get(pk, 0)
                    prov_ok = got < cap

                pool_ok = True
                if use_pool and req_pool:
                    pool_ok = executor_pool_remaining.get(req_pool, 0) > 0

                if prov_ok and pool_ok:
                    dq.pop(0)
                    jid = int(row["id"])
                    chosen.append(jid)
                    per_type_picked[t] += 1
                    picked_by_prov[pk] = picked_by_prov.get(pk, 0) + 1
                    if req_pool and use_pool and executor_pool_remaining is not None:
                        executor_pool_remaining[req_pool] = executor_pool_remaining.get(req_pool, 0) - 1
                        picked_by_pool[req_pool] = picked_by_pool.get(req_pool, 0) + 1
                    progress = True
                    picked_here = True
                    break
                dq.append(dq.pop(0))
            if not picked_here:
                continue
        if not progress:
            break

    for t in types:
        skipped_by_type[t] = len(deques[t])
    skipped_total = sum(skipped_by_type.values())

    rem_by_prov: dict[str, int] = {}
    rem_by_pool: dict[str, int] = {}
    for t in types:
        for row in deques[t]:
            pk = _normalize_provider_key(row)
            rem_by_prov[pk] = rem_by_prov.get(pk, 0) + 1
            rp = str(row.get("required_executor_class") or "").strip().lower()
            if rp:
                rem_by_pool[rp] = rem_by_pool.get(rp, 0) + 1
    meta["picked_by_provider"] = picked_by_prov
    meta["remaining_by_provider"] = rem_by_prov
    meta["picked_by_executor_pool"] = picked_by_pool
    meta["remaining_by_required_executor_pool"] = rem_by_pool
    if use_prov and provider_effective_caps is not None:
        est = 0
        for pk, nrem in rem_by_prov.items():
            cap = int(provider_effective_caps.get(pk, effective_max))
            picked = picked_by_prov.get(pk, 0)
            if nrem > 0 and picked >= cap:
                est += nrem
        meta["skipped_due_to_provider_cap_est"] = est
    if use_pool and executor_pool_remaining is not None:
        est_pool = 0
        for rp, nrem in rem_by_pool.items():
            if nrem > 0 and executor_pool_remaining.get(rp, 0) <= 0:
                est_pool += nrem
        meta["skipped_due_to_executor_pool_est"] = est_pool

    return chosen, skipped_by_type, skipped_total, meta


def plan_dispatch(
    conn: Any,
    candidate_rows: list[dict[str, Any]],
    policy: DispatchPolicy | None = None,
) -> DispatchPlan:
    """
    Compute which job ids to actually ``send_task`` this round.

    ``candidate_rows`` should be scheduler-relevant work (e.g. runnable ANALYZE_SESSION
    for ingest). Missing / stale rows are ignored upstream.
    """
    p = policy or DispatchPolicy.from_env()
    from services.worker_pools import required_executor_class_for_job

    for r in candidate_rows:
        if "required_executor_class" not in r:
            r["required_executor_class"] = required_executor_class_for_job(dict(r))
    h = cluster_headroom_for_dispatch(conn)
    headroom = int(h.get("headroom", 0))
    total_capacity = int(h.get("total_capacity", 0))
    total_rows = int(h.get("total_worker_rows", 0))
    n = len(candidate_rows)
    if p.respect_headroom:
        if total_capacity > 0:
            effective_max = min(p.max_per_round, headroom)
        elif total_rows > 0:
            effective_max = 0
        else:
            effective_max = p.max_per_round
    else:
        effective_max = p.max_per_round

    prov_keys = {_normalize_provider_key(r) for r in candidate_rows}
    sqlite_sig: dict[str, Any] | None = None
    prov_caps: dict[str, int] | None = None
    explain: dict[str, Any] | None = None
    scope_ns, scope_pk = dispatch_scope_from_env()

    if (
        p.use_provider_runtime_signals
        and effective_max > 0
        and candidate_rows
        and prov_keys
    ):
        sqlite_sig = gather_provider_dispatch_signals(
            conn,
            finished_window_seconds=p.finished_stats_window_seconds,
            namespace=scope_ns,
            project_key=scope_pk,
        )
        prov_caps, explain = _effective_caps_per_provider(
            p,
            sqlite_signals=sqlite_sig,
            provider_keys=prov_keys,
            effective_global_max=effective_max,
        )

    ep_snapshot: dict[str, Any] = h.get("executor_pools") or {}
    pool_eff = dict(ep_snapshot.get("effective_headroom_by_required_pool") or {})
    pool_remaining: dict[str, int] | None = (
        dict(pool_eff) if effective_max > 0 and pool_eff else None
    )

    selected, skipped_by_type, skipped_total, sel_meta = select_jobs_weighted_fair(
        candidate_rows,
        effective_max=effective_max,
        policy=p,
        provider_effective_caps=prov_caps,
        executor_pool_remaining=pool_remaining,
    )

    id_to_type = {int(r["id"]): str(r.get("job_type") or "UNKNOWN") for r in candidate_rows}
    by_type_chosen: dict[str, int] = {}
    for jid in selected:
        t = id_to_type.get(int(jid), "UNKNOWN")
        by_type_chosen[t] = by_type_chosen.get(t, 0) + 1
    note = None
    if p.respect_headroom and total_capacity > 0 and headroom == 0 and n > 0:
        note = "headroom_zero_no_dispatch"
    elif p.respect_headroom and total_rows > 0 and total_capacity == 0 and n > 0:
        note = "no_online_workers_no_dispatch"

    decisions: dict[str, Any] = {
        "mode": "worker_headroom_and_fairness"
        + ("+provider_runtime" if prov_caps is not None else "")
        + "+executor_pool",
        "dispatch_scope_env": {"namespace": scope_ns, "project_key": scope_pk},
        "selection": sel_meta,
        "executor_pools": ep_snapshot,
    }
    if explain is not None:
        decisions["provider_explain"] = explain
    if sqlite_sig is not None:
        # Compact: avoid huge blobs in logs
        decisions["sqlite_signals"] = {
            "finished_window_seconds": sqlite_sig.get("finished_window_seconds"),
            "inflight_by_provider": sqlite_sig.get("inflight_by_provider"),
            "finished_stats_keys": sorted(
                (sqlite_sig.get("finished_stats_by_provider") or {}).keys()
            ),
        }

    return DispatchPlan(
        policy=p,
        headroom=headroom,
        online_workers=int(h.get("online_workers", 0)),
        total_worker_rows=total_rows,
        total_capacity=int(h.get("total_capacity", 0)),
        total_inflight=int(h.get("total_inflight", 0)),
        effective_max=effective_max,
        candidate_count=n,
        selected_job_ids=selected,
        by_type_chosen=by_type_chosen,
        skipped_by_type=skipped_by_type,
        skipped_total_over_cap=skipped_total,
        note=note,
        provider_effective_caps=prov_caps,
        provider_signals=sqlite_sig,
        dispatch_decisions=decisions,
        executor_pools_snapshot=ep_snapshot if ep_snapshot else None,
    )


def plan_dispatch_for_job_ids(
    conn: Any,
    job_ids: list[int],
    policy: DispatchPolicy | None = None,
) -> DispatchPlan:
    """Load dispatch metadata and run :func:`plan_dispatch`."""
    rows = get_jobs_dispatch_metadata(conn, job_ids)
    return plan_dispatch(conn, rows, policy=policy)


def by_type_counts(job_ids: list[int], conn: Any) -> dict[str, int]:
    """Helper for logging: count job types for a list of ids (requires DB)."""
    if not job_ids:
        return {}
    rows = get_jobs_dispatch_metadata(conn, job_ids)
    out: dict[str, int] = {}
    for r in rows:
        jt = str(r.get("job_type") or "UNKNOWN")
        out[jt] = out.get(jt, 0) + 1
    return out
