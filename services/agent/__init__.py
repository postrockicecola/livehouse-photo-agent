"""Agentic curation layer over the Stage1/2/3 pipeline.

A ReAct-style loop where an LLM (or deterministic heuristic) planner decides, step
by step and under an inference budget, which photos to inspect cheaply, which to
deep-analyze with the VLM, when to escalate shaky results to a stronger model, and
when to commit a final selection. Tools wrap existing capabilities and ride the
real ``inference`` layer, so the agent reuses production serving infra rather than
a parallel model path.

Entry point: :class:`~services.agent.loop.CurationAgent`.
"""
from __future__ import annotations

from services.agent.llm_backend import (
    build_curation_llm_planner,
    build_curation_llm_planner_from_config,
    build_planner_complete_fn,
)
from services.agent.loop import CurationAgent
from services.agent.planner import HeuristicPlanner, LLMPlanner, Planner
from services.agent.reflection import ReflectionVerdict, reflect, validate_analysis
from services.agent.tools import (
    AnalyzeTool,
    FinalizeTool,
    InspectTool,
    ToolRegistry,
    build_stage3_analyze_fn,
)
from services.agent.types import (
    ActionType,
    AgentConfig,
    AgentResult,
    AgentState,
    AgentStep,
    Candidate,
    ToolCall,
    ToolResult,
)

__all__ = [
    "ActionType",
    "AgentConfig",
    "AgentResult",
    "AgentState",
    "AgentStep",
    "AnalyzeTool",
    "Candidate",
    "CurationAgent",
    "FinalizeTool",
    "HeuristicPlanner",
    "build_curation_llm_planner",
    "build_curation_llm_planner_from_config",
    "build_planner_complete_fn",
    "InspectTool",
    "LLMPlanner",
    "Planner",
    "ReflectionVerdict",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "build_stage3_analyze_fn",
    "reflect",
    "validate_analysis",
]
