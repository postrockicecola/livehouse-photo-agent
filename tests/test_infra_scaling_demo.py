"""Tests for the load→backpressure→throttle→KEDA end-to-end demo (scripts/infra_scaling_demo.py).

The demo drives the *real* dispatch-throttle engine and the *real* KEDA Redis-list formula
with parsed manifest thresholds, so these assert the wiring and the control-loop invariants
(scale up/down, throttle under provider pressure, backpressure before pods arrive) offline.
"""
from __future__ import annotations

from pathlib import Path

from scripts.infra_scaling_demo import (
    DemoParams,
    build_demo_policy,
    keda_desired_replicas,
    parse_keda_scaledobject,
    simulate,
    _throttle_slots_and_pressure,
)

_VLM_MANIFEST = Path(__file__).resolve().parent.parent / "deploy" / "k8s" / "61-keda-vlm.yaml"


def test_parse_keda_scaledobject_reads_real_thresholds():
    spec = parse_keda_scaledobject(_VLM_MANIFEST)
    assert spec.list_name == "vlm"
    assert spec.list_length == 3
    assert spec.min_replicas == 1
    assert spec.max_replicas == 4


def test_keda_formula_matches_ceil_over_listlength_clamped():
    spec = parse_keda_scaledobject(_VLM_MANIFEST)  # listLength 3, [1, 4]
    assert keda_desired_replicas(0, spec) == 1        # empty → floor at minReplicas
    assert keda_desired_replicas(4, spec) == 2        # ceil(4/3)
    assert keda_desired_replicas(9, spec) == 3        # ceil(9/3)
    assert keda_desired_replicas(1000, spec) == 4     # clamped to maxReplicas


def test_throttle_cuts_slots_and_raises_pressure_under_provider_stress():
    policy = build_demo_policy(dispatch_cap=16)
    healthy_slots, healthy_p = _throttle_slots_and_pressure(
        policy, latency_ms=600, failure_rate=0.0, inflight=0
    )
    # Fresh EMA per call sequence would carry over; use a distinct call to compare a
    # clearly-stressed provider against the healthy baseline.
    stressed_slots, stressed_p = _throttle_slots_and_pressure(
        policy, latency_ms=6000, failure_rate=0.6, inflight=16
    )
    assert stressed_p > healthy_p
    assert stressed_slots < healthy_slots
    assert stressed_slots >= policy.per_provider_min_slots


def test_simulation_shows_full_control_loop():
    spec = parse_keda_scaledobject(_VLM_MANIFEST)
    params = DemoParams()
    policy = build_demo_policy(params.dispatch_cap)
    report = simulate(spec, params, policy)
    s = report["series"]
    sm = report["summary"]

    # Series is complete and replicas always stay within the KEDA bounds.
    assert len(s["tick"]) == params.ticks
    assert all(spec.min_replicas <= r <= spec.max_replicas for r in s["replicas"])

    # Load burst drives KEDA to the ceiling and, after cooldown, back to the floor.
    assert sm["peak_replicas"] == spec.max_replicas
    assert s["replicas"][-1] == spec.min_replicas

    # The provider incident engages the throttle: effective slots fall below the base cap.
    assert sm["min_effective_slots_during_incident"] < params.dispatch_cap
    assert sm["peak_pressure"] > 0.3

    # A burst hitting before KEDA's pods come online produces real backpressure.
    assert sm["peak_backpressure"] > 0

    # Pressure is materially higher inside the incident window than at rest.
    incident = s["pressure"][params.incident_start : params.incident_end]
    baseline = s["pressure"][:params.burst_start]
    assert max(incident) > max(baseline)
