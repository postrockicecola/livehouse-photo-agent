"""L0 search-gap smoke (CONTRACT Phase 5): in-table probes must not regress."""
from __future__ import annotations

from quality.agent_cases import session_dir
from scripts.eval.measure_gallery_search_gaps import probes_from_agent_cases, run_session


def test_smoke_fixture_no_in_table_synonym_gap() -> None:
    """OOV paraphrases may legitimately miss; in-table / case-derived probes must not."""
    report = run_session(
        session_path=session_dir("smoke"),
        label="smoke_fixture",
        extra_probes=probes_from_agent_cases(),
    )
    regressed = [
        p
        for p in report["probes"]
        if p["classification"] == "empty_synonym_gap" and "OOV" not in p["notes"]
    ]
    assert not regressed, regressed
