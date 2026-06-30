"""Automated checks for ``reliability_scenarios`` (isolated SQLite, no Celery)."""
from __future__ import annotations

import pytest

from reliability_scenarios import ALL_SCENARIOS, SCENARIO_BY_ID, run_all_scenarios


@pytest.mark.parametrize(
    "scenario_fn",
    ALL_SCENARIOS,
    ids=[fn.__name__ for fn in ALL_SCENARIOS],
)
def test_chaos_scenario_passes(scenario_fn):
    r = scenario_fn()
    assert r.ok, f"{r.id}: {r.evidence}"


def test_scenario_registry_covers_all_functions():
    assert len(SCENARIO_BY_ID) == len(ALL_SCENARIOS)
    for fn in ALL_SCENARIOS:
        r = fn()
        assert r.id in SCENARIO_BY_ID
        assert SCENARIO_BY_ID[r.id] is fn


def test_run_all_count():
    assert len(run_all_scenarios()) == len(ALL_SCENARIOS)
