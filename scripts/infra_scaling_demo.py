#!/usr/bin/env python3
"""End-to-end AI-infra control-loop demo: load → backpressure → throttle → KEDA scale.

This makes the three infra mechanisms that protect the VLM visible in one reproducible
run, and — importantly — drives them with the **real** production math, not a cartoon:

1. **Dispatch throttle** — each tick's per-provider cap comes from the actual scheduler
   engine (`services.scheduler._effective_caps_per_provider`): blended failure / inflight
   / latency pressure with EMA smoothing, so ``effective_slots ≈ base·(1 − strength·pressure)``.
2. **KEDA autoscaling** — desired worker replicas use the real KEDA Redis-list formula
   ``ceil(LLEN / listLength)`` clamped to ``[min, max]``, with ``listLength`` / bounds
   parsed straight from ``deploy/k8s/*keda*.yaml``.
3. **Inference backpressure** — workers drain a bounded in-process admission window
   (``replicas · inflight_per_replica``); demand above it is backpressure (waits for a slot).

The queue *dynamics* around those two real components are a transparent discrete-tick
model (clearly a simulation) driven by two exogenous profiles: a **load burst** (fills the
broker → KEDA scales up) and a separate **provider incident** (latency/failure spike →
throttle cuts dispatch to protect the provider). It emits a JSON document + a
dependency-free SVG with the coupled curves.

Example::

    python scripts/infra_scaling_demo.py --pool vlm --out reports/infra_scaling.json --svg reports/infra_scaling.svg
"""
from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.scheduler import DispatchPolicy, _effective_caps_per_provider  # noqa: E402
from services.scheduler import _LAST_PROVIDER_PRESSURE_EMA  # noqa: E402

DEMO_PROVIDER = "ollama"


# --------------------------------------------------------------- KEDA manifest


@dataclass
class KedaSpec:
    name: str
    list_name: str
    list_length: int
    min_replicas: int
    max_replicas: int
    cooldown_period: int = 30


def parse_keda_scaledobject(path: str | Path) -> KedaSpec:
    """Parse the fields the demo needs from a KEDA ScaledObject manifest (no yaml dep)."""
    text = Path(path).read_text(encoding="utf-8")

    def _int(pattern: str, default: int) -> int:
        m = re.search(pattern, text)
        return int(m.group(1)) if m else default

    def _str(pattern: str, default: str) -> str:
        m = re.search(pattern, text)
        return m.group(1) if m else default

    return KedaSpec(
        name=_str(r"name:\s*([\w-]+)", "worker"),
        list_name=_str(r"listName:\s*([\w-]+)", "celery"),
        list_length=_int(r'listLength:\s*"?(\d+)"?', 5),
        min_replicas=_int(r"minReplicaCount:\s*(\d+)", 1),
        max_replicas=_int(r"maxReplicaCount:\s*(\d+)", 6),
        cooldown_period=_int(r"cooldownPeriod:\s*(\d+)", 30),
    )


def keda_desired_replicas(backlog: int, spec: KedaSpec) -> int:
    """The real KEDA Redis-list scaler: ceil(LLEN / listLength) clamped to [min, max]."""
    raw = math.ceil(backlog / max(1, spec.list_length)) if backlog > 0 else 0
    return max(spec.min_replicas, min(spec.max_replicas, raw))


# --------------------------------------------------------------- throttle (real engine)


def _throttle_slots_and_pressure(
    policy: DispatchPolicy, *, latency_ms: float, failure_rate: float, inflight: int, window_volume: int = 20
) -> tuple[int, float]:
    """Call the production dispatch engine for one provider; return (effective_slots, pressure)."""
    failed = int(round(window_volume * max(0.0, min(1.0, failure_rate))))
    signals = {
        "inflight_by_provider": {DEMO_PROVIDER: int(inflight)},
        "finished_stats_by_provider": {
            DEMO_PROVIDER: {
                "succeeded": window_volume - failed,
                "failed_terminal": failed,
                "avg_inference_ms": float(latency_ms),
                "avg_total_latency_ms": float(latency_ms),
            }
        },
    }
    caps, explain = _effective_caps_per_provider(
        policy,
        sqlite_signals=signals,
        provider_keys={DEMO_PROVIDER},
        effective_global_max=policy.default_per_provider_max,
    )
    providers = explain.get("providers", {})
    pressure = float(providers.get(DEMO_PROVIDER, {}).get("pressure", 0.0))
    return int(caps[DEMO_PROVIDER]), pressure


# --------------------------------------------------------------- simulation


@dataclass
class DemoParams:
    ticks: int = 60
    burst_start: int = 6
    burst_end: int = 18
    burst_arrivals: int = 24
    idle_arrivals: int = 2
    incident_start: int = 26
    incident_end: int = 38
    base_latency_ms: float = 700.0
    incident_latency_ms: float = 3200.0
    base_failure: float = 0.02
    incident_failure: float = 0.45
    inflight_per_replica: int = 5        # in-process admission slots per replica (bounds concurrency)
    dispatch_cap: int = 16               # base per-provider dispatch cap before throttle


@dataclass
class Series:
    tick: list[int] = field(default_factory=list)
    arrivals: list[int] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)
    dispatched: list[int] = field(default_factory=list)
    broker_backlog: list[int] = field(default_factory=list)
    replicas: list[int] = field(default_factory=list)
    effective_slots: list[int] = field(default_factory=list)
    pressure: list[float] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)
    failure_rate: list[float] = field(default_factory=list)
    max_inflight: list[int] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)
    backpressure: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, list[Any]]:
        return {k: list(v) for k, v in self.__dict__.items()}


def simulate(spec: KedaSpec, params: DemoParams, policy: DispatchPolicy) -> dict[str, Any]:
    # Reset the engine's per-provider EMA so the run is reproducible tick-to-tick.
    _LAST_PROVIDER_PRESSURE_EMA.pop(DEMO_PROVIDER, None)

    s = Series()
    pending = 0
    broker = 0
    replicas = spec.min_replicas
    util_prev = 0.0
    inflight_prev = 0
    low_backlog_streak = 0
    cooldown_ticks = max(1, spec.cooldown_period // 5)  # pollingInterval≈5s → cooldown in ticks

    for t in range(params.ticks):
        arrivals = params.burst_arrivals if params.burst_start <= t < params.burst_end else params.idle_arrivals
        incident = params.incident_start <= t < params.incident_end
        latency = (params.incident_latency_ms if incident else params.base_latency_ms) * (1.0 + 0.5 * util_prev)
        failure = params.incident_failure if incident else params.base_failure

        slots, pressure = _throttle_slots_and_pressure(
            policy, latency_ms=latency, failure_rate=failure, inflight=inflight_prev
        )

        pending += arrivals
        dispatched = min(pending, slots)
        pending -= dispatched
        broker += dispatched
        broker_observed = broker  # the LLEN KEDA polls this tick, before workers drain it

        # Workers drain the broker through the bounded inference admission window, using the
        # replicas that are ALREADY running this tick (new pods from KEDA arrive next tick —
        # the scale-up lag is what makes a burst show up as backpressure before relief).
        # Concurrency is capped at max_inflight; throughput = that window turning over at the
        # current latency (slots turn over slower when the provider is slow), so latency
        # throttles both dispatch (pressure) and drain rate — why KEDA earns its keep.
        max_inflight = replicas * params.inflight_per_replica
        throughput = max_inflight * (params.base_latency_ms / max(1.0, latency))
        completed = min(broker, int(round(throughput)))
        # Anything queued beyond the admission window this tick is waiting for a slot.
        backpressure = max(0, broker_observed - max_inflight)
        occupancy = min(broker_observed, max_inflight)
        broker -= completed

        inflight_prev = occupancy
        util_prev = occupancy / max_inflight if max_inflight else 0.0

        s.tick.append(t)
        s.arrivals.append(arrivals)
        s.pending.append(pending)
        s.dispatched.append(dispatched)
        s.broker_backlog.append(broker_observed)
        s.replicas.append(replicas)
        s.effective_slots.append(slots)
        s.pressure.append(round(pressure, 4))
        s.latency_ms.append(round(latency, 1))
        s.failure_rate.append(round(failure, 4))
        s.max_inflight.append(max_inflight)
        s.completed.append(completed)
        s.backpressure.append(backpressure)

        # KEDA polls the observed backlog and sets the replica count for the NEXT tick:
        # scale up immediately, scale down only after the backlog stays low for a cooldown.
        desired = keda_desired_replicas(broker_observed, spec)
        if desired > replicas:
            replicas = desired
            low_backlog_streak = 0
        elif desired < replicas:
            low_backlog_streak += 1
            if low_backlog_streak >= cooldown_ticks:
                replicas = desired
                low_backlog_streak = 0
        else:
            low_backlog_streak = 0

    incident_slots = [s.effective_slots[t] for t in range(params.ticks) if params.incident_start <= t < params.incident_end]
    summary = {
        "peak_broker_backlog": max(s.broker_backlog),
        "peak_replicas": max(s.replicas),
        "min_replicas": min(s.replicas),
        "base_dispatch_cap": params.dispatch_cap,
        "min_effective_slots_during_incident": min(incident_slots) if incident_slots else None,
        "peak_pressure": max(s.pressure),
        "peak_backpressure": max(s.backpressure),
        "total_dispatched": sum(s.dispatched),
        "total_completed": sum(s.completed),
    }
    return {
        "keda": {
            "scaled_object": spec.name,
            "list_name": spec.list_name,
            "list_length": spec.list_length,
            "min_replicas": spec.min_replicas,
            "max_replicas": spec.max_replicas,
        },
        "policy": {
            "dispatch_cap": params.dispatch_cap,
            "throttle_strength": policy.provider_throttle_strength,
            "latency_soft_limit_ms": policy.latency_soft_limit_ms,
            "pressure_ema_alpha": policy.pressure_ema_alpha,
        },
        "params": params.__dict__,
        "series": s.as_dict(),
        "summary": summary,
    }


# --------------------------------------------------------------- SVG


def _polyline(vals: list[float], x0: float, y0: float, w: float, h: float, vmax: float) -> str:
    vmax = max(vmax, 1e-9)
    n = len(vals)
    pts = []
    for i, v in enumerate(vals):
        x = x0 + (w * i / max(1, n - 1))
        y = y0 + h - (h * (v / vmax))
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _panel(title: str, x: float, y: float, w: float, h: float, series: list[tuple[str, list[float], str]]) -> str:
    parts = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#0b0b12" stroke="#2a2a3a"/>',
        f'<text x="{x+8}" y="{y+16}" fill="#cbd5e1" font-family="monospace" font-size="12">{title}</text>',
    ]
    vmax = max((max(v) if v else 0) for _, v, _ in series)
    px, py, pw, ph = x + 8, y + 26, w - 16, h - 42
    for i, (label, vals, color) in enumerate(series):
        parts.append(
            f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{_polyline(vals, px, py, pw, ph, vmax)}"/>'
        )
        parts.append(
            f'<text x="{x+8+i*130}" y="{y+h-8}" fill="{color}" font-family="monospace" font-size="11">{label}</text>'
        )
    return "".join(parts)


def render_svg(report: dict[str, Any]) -> str:
    s = report["series"]
    W, H = 900, 560
    pad = 20
    pw = W - 2 * pad
    ph = (H - 4 * pad) / 3
    panels = [
        _panel(
            "Load → autoscaling  (arrivals · broker backlog · KEDA replicas)",
            pad, pad, pw, ph,
            [
                ("arrivals", [float(v) for v in s["arrivals"]], "#38bdf8"),
                ("broker_backlog", [float(v) for v in s["broker_backlog"]], "#f59e0b"),
                ("replicas", [float(v) for v in s["replicas"]], "#34d399"),
            ],
        ),
        _panel(
            "Dispatch throttle  (provider pressure · effective slots)",
            pad, pad * 2 + ph, pw, ph,
            [
                ("pressure x100", [float(v) * 100 for v in s["pressure"]], "#f472b6"),
                ("effective_slots", [float(v) for v in s["effective_slots"]], "#a78bfa"),
            ],
        ),
        _panel(
            "Inference backpressure  (max_inflight · completed · backpressure waits)",
            pad, pad * 3 + ph * 2, pw, ph,
            [
                ("max_inflight", [float(v) for v in s["max_inflight"]], "#64748b"),
                ("completed", [float(v) for v in s["completed"]], "#22d3ee"),
                ("backpressure", [float(v) for v in s["backpressure"]], "#ef4444"),
            ],
        ),
    ]
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
        f'<rect width="{W}" height="{H}" fill="#05050a"/>' + "".join(panels) + "</svg>"
    )


# --------------------------------------------------------------- CLI


def _default_manifest(pool: str) -> str:
    return "deploy/k8s/61-keda-vlm.yaml" if pool == "vlm" else "deploy/k8s/60-keda-scaledobject.yaml"


def build_demo_policy(dispatch_cap: int) -> DispatchPolicy:
    """A legible throttle policy for the demo: a lower latency soft-limit so the latency
    signal actually contributes on sub-second-to-few-second VLM calls."""
    return DispatchPolicy(
        default_per_provider_max=dispatch_cap,
        provider_throttle_strength=1.0,
        latency_soft_limit_ms=1500,
        latency_pressure_span_ms=2500,
        pressure_ema_alpha=0.4,
        per_provider_min_slots=1,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pool", choices=["general", "vlm"], default="vlm")
    ap.add_argument("--manifest", default=None, help="KEDA ScaledObject yaml (default: by --pool)")
    ap.add_argument("--ticks", type=int, default=60)
    ap.add_argument("--burst", type=int, default=24, help="arrivals/tick during the load burst")
    ap.add_argument("--dispatch-cap", type=int, default=16, help="base per-provider dispatch cap before throttle")
    ap.add_argument("--out", default=None, help="write the JSON report here")
    ap.add_argument("--svg", default=None, help="write the SVG chart here")
    args = ap.parse_args()

    spec = parse_keda_scaledobject(args.manifest or _default_manifest(args.pool))
    params = DemoParams(ticks=args.ticks, burst_arrivals=args.burst, dispatch_cap=args.dispatch_cap)
    policy = build_demo_policy(args.dispatch_cap)
    report = simulate(spec, params, policy)

    sm = report["summary"]
    print(
        f"\n=== infra scaling demo (pool={args.pool}, KEDA {spec.list_name} listLength={spec.list_length} "
        f"replicas {spec.min_replicas}..{spec.max_replicas}) ==="
    )
    print(f"load burst      → peak broker backlog {sm['peak_broker_backlog']}, KEDA scaled to {sm['peak_replicas']} replicas")
    print(f"provider incident → pressure peaked {sm['peak_pressure']:.2f}, dispatch throttled "
          f"{params.dispatch_cap} → {sm['min_effective_slots_during_incident']} slots")
    print(f"backpressure    → peak {sm['peak_backpressure']} requests waiting on an admission slot")

    if args.out:
        import json

        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {p}")
    if args.svg:
        p = Path(args.svg)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_svg(report), encoding="utf-8")
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
