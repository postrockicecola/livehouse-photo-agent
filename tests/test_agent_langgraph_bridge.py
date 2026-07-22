"""LangGraph is the default curation runtime (multi-node plan→act→reflect)."""
from __future__ import annotations

import pytest

from services.agent.graph import (
    LANGGRAPH_MAPPING,
    compile_curation_graph,
    langgraph_available,
    mapping_table,
    run_curation_graph,
)
from services.agent.loop import CurationAgent
from services.agent.planner import HeuristicPlanner
from services.agent.tools import AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import ActionType, AgentConfig, Candidate


def _registry(analyze_fn) -> ToolRegistry:
    return ToolRegistry(
        inspect=InspectTool(),
        analyze=AnalyzeTool(analyze_fn, default_tier="fast"),
        finalize=FinalizeTool(),
    )


def _cands() -> list[Candidate]:
    return [
        Candidate(
            image_id="a",
            image_path="/tmp/a.jpg",
            features={"fast_score": 0.9, "tech_score": 0.8},
        ),
        Candidate(
            image_id="b",
            image_path="/tmp/b.jpg",
            features={"fast_score": 0.2, "tech_score": 0.3},
        ),
    ]


def _analyze(path: str, tier: str):
    score = 80.0 if path.endswith("a.jpg") else 40.0
    return {"score": score, "confidence": 0.9, "dimensions": {}, "verdict": "keep"}


@pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")
def test_mapping_and_compiled_nodes():
    assert "planner.next_action" in mapping_table()
    assert mapping_table() == LANGGRAPH_MAPPING
    app = compile_curation_graph(
        tools=_registry(_analyze),
        config=AgentConfig(max_steps=20, max_inferences=2, allow_escalation=False),
        planner=HeuristicPlanner(),
    )
    # Compiled graph exposes the three ReAct nodes.
    graph = app.get_graph()
    names = {n for n in graph.nodes if n not in ("__start__", "__end__")}
    assert {"plan", "act", "reflect"} <= names


@pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")
def test_run_curation_graph_backend_langgraph():
    cfg = AgentConfig(max_inferences=2, max_analyze_candidates=2, max_steps=50, allow_escalation=False)
    result = run_curation_graph(_cands(), tools=_registry(_analyze), config=cfg)
    assert result.metrics.get("backend") == "langgraph"
    assert result.metrics["steps"] >= 1
    assert result.steps[-1].call.action == ActionType.FINALIZE


@pytest.mark.skipif(not langgraph_available(), reason="langgraph not installed")
def test_curation_agent_defaults_to_langgraph(monkeypatch):
    monkeypatch.delenv("LIVEHOUSE_AGENT_RUNTIME", raising=False)
    agent = CurationAgent(
        tools=_registry(_analyze),
        config=AgentConfig(max_inferences=2, max_analyze_candidates=2, max_steps=50, allow_escalation=False),
    )
    res = agent.run(_cands())
    assert res.metrics.get("backend") == "langgraph"


def test_curation_agent_imperative_override(monkeypatch):
    monkeypatch.setenv("LIVEHOUSE_AGENT_RUNTIME", "imperative")
    agent = CurationAgent(
        tools=_registry(_analyze),
        config=AgentConfig(max_inferences=2, max_analyze_candidates=2, max_steps=50, allow_escalation=False),
    )
    res = agent.run(_cands())
    assert res.metrics.get("backend") == "imperative"
