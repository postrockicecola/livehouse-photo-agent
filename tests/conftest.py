"""Shared pytest hooks for Livehouse agent / pipeline tests.

Production-path guard
--------------------
Tests marked ``@pytest.mark.requires_langgraph`` verify the Gallery chat
**production** runtime (LangGraph). If ``langgraph`` is not importable, those
tests must **fail** (not skip, not silently fall back to ``imperative``).

Silent degradation previously hid real gaps: the suite went green while never
exercising ``conversation_graph.answer()`` branches that ship in production.
"""
from __future__ import annotations

import pytest

LANGGRAPH_REQUIRED_FAIL_MSG = (
    "This test is marked requires_langgraph and must verify the production "
    "Gallery chat runtime (LangGraph). langgraph is not installed in this "
    "environment, so the production path cannot be validated. "
    "Install with: pip install 'langgraph>=0.2' "
    "(see requirements.txt). Do not set LIVEHOUSE_AGENT_RUNTIME=imperative "
    "to make this pass — that would reintroduce silent path skew."
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_langgraph: production LangGraph path — FAIL if langgraph missing (not skip)",
    )


def enforce_langgraph_available() -> None:
    """Raise ``pytest.fail`` when production-path tests cannot run on LangGraph."""
    from services.agent.conversation_graph import langgraph_available

    if not langgraph_available():
        pytest.fail(LANGGRAPH_REQUIRED_FAIL_MSG)


@pytest.fixture(autouse=True)
def _enforce_requires_langgraph(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Fail hard when a production-path test cannot run on LangGraph."""
    if request.node.get_closest_marker("requires_langgraph") is None:
        return

    enforce_langgraph_available()
    # Block silent imperative override for production-path tests.
    monkeypatch.delenv("LIVEHOUSE_AGENT_RUNTIME", raising=False)
