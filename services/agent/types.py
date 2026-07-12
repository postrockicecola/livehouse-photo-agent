"""Core types for the Livehouse curation agent (ReAct-style tool-using loop).

This is an *agentic* layer on top of the fixed Stage1/2/3 pipeline: instead of a
hard-coded "score everything then threshold" flow, an LLM/heuristic planner
decides — step by step, under an inference budget — which photos to inspect
cheaply, which to deep-analyze with the VLM, when to escalate a low-confidence
result to a stronger model, and when to stop and commit a final selection.

Nothing here imports the heavy pipeline; the agent talks to the rest of the
system only through injected tool callables (see ``services/agent/tools.py``),
so the whole loop runs (and is unit-tested) without a GPU or live model server.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ActionType(str, Enum):
    """The tools the planner can invoke each step."""

    INSPECT = "inspect"            # cheap, no model: pull Stage1/2 features for one candidate
    ANALYZE = "analyze"            # one VLM call (fast or full tier) on one candidate
    COMPARE = "compare"           # zero-cost: relative judgement between two candidates
    CLUSTER = "cluster"           # zero-cost: group burst / near-duplicate frames (once)
    QUERY_GALLERY = "query_gallery"  # zero-cost: recall a prior committed score for one image
    FINALIZE = "finalize"         # terminal: commit the keeper set + rationale


@dataclass
class Candidate:
    """One photo flowing through the agent, accumulating observations over time."""

    image_id: str
    image_path: str
    features: dict[str, Any] = field(default_factory=dict)
    inspected: bool = False
    analysis: Optional[dict[str, Any]] = None
    score: Optional[float] = None
    confidence: Optional[float] = None
    tier: Optional[str] = None         # model tier that produced the current analysis
    attempts: int = 0                  # number of ANALYZE calls spent on this image
    escalated: bool = False            # whether it has been re-analyzed at a higher tier

    @property
    def analyzed(self) -> bool:
        return self.analysis is not None and not self.analysis.get("error")

    def fast_score(self) -> float:
        try:
            return float(self.features.get("fast_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0


@dataclass
class ToolCall:
    """A single planner decision: which tool, on which candidate, and why."""

    action: ActionType
    image_id: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""                   # planner "thought" (for the trace)
    source: str = "heuristic"          # "heuristic" | "llm" | "llm_fallback" | "reflection"


@dataclass
class ToolResult:
    """The observation returned by a tool, plus accounting for budget/metrics."""

    ok: bool
    observation: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    inference_cost: int = 0            # VLM calls consumed by this step
    latency_ms: int = 0


@dataclass
class AgentStep:
    """One full observe→act cycle, recorded for the structured trace."""

    index: int
    call: ToolCall
    result: ToolResult
    reflection: Optional[str] = None   # set when the step triggered an escalation


@dataclass
class AgentConfig:
    """Decision thresholds and budgets for the curation agent."""

    target_keepers: int = 10
    keep_score_threshold: float = 70.0
    confidence_floor: float = 0.80
    ambiguous_band: tuple[float, float] = (65.0, 75.0)
    # Only deep-analyze candidates whose cheap fast_score clears this floor.
    analyze_fast_score_floor: float = 0.0
    # Cap how many images reach the VLM regardless of remaining inference budget.
    max_analyze_candidates: int = 30
    # Hard loop guards (the second is the cost knob that ties into A's narrative).
    max_steps: int = 200
    max_inferences: int = 60
    # Reflection / escalation.
    allow_escalation: bool = True
    base_tier: str = "fast"
    escalation_tier: str = "full"

    def __post_init__(self) -> None:
        lo, hi = self.ambiguous_band
        if lo > hi:
            raise ValueError(f"ambiguous_band must be (lo<=hi), got {self.ambiguous_band!r}")


@dataclass
class AgentState:
    """Mutable working memory shared between the loop, planner and tools."""

    candidates: dict[str, Candidate]
    config: AgentConfig
    order: list[str] = field(default_factory=list)
    step_index: int = 0
    inferences_used: int = 0
    escalations: int = 0
    pending_escalations: list[str] = field(default_factory=list)
    finalized: bool = False
    selected: list[str] = field(default_factory=list)
    clustered: bool = False  # set once the CLUSTER tool has grouped the candidates

    @classmethod
    def from_candidates(cls, candidates: list[Candidate], config: AgentConfig) -> "AgentState":
        ordered = list(candidates)
        return cls(
            candidates={c.image_id: c for c in ordered},
            config=config,
            order=[c.image_id for c in ordered],
        )

    def ordered_candidates(self) -> list[Candidate]:
        return [self.candidates[i] for i in self.order]

    def not_inspected(self) -> list[Candidate]:
        return [c for c in self.ordered_candidates() if not c.inspected]

    def analyzable(self) -> list[Candidate]:
        """Inspected, not-yet-analyzed candidates that clear the fast-score floor."""
        floor = self.config.analyze_fast_score_floor
        return [
            c
            for c in self.ordered_candidates()
            if c.inspected and c.analysis is None and c.fast_score() >= floor
        ]

    def analyzed_count(self) -> int:
        return sum(1 for c in self.candidates.values() if c.attempts > 0)

    def budget_exhausted(self) -> bool:
        return self.inferences_used >= self.config.max_inferences

    def can_analyze_more(self) -> bool:
        return (
            not self.budget_exhausted()
            and self.analyzed_count() < self.config.max_analyze_candidates
        )

    def current_keepers(self) -> list[Candidate]:
        """Analyzed candidates above threshold, best-first, capped to target."""
        kept = [
            c
            for c in self.candidates.values()
            if c.analyzed and (c.score or 0.0) >= self.config.keep_score_threshold
        ]
        kept.sort(key=lambda c: (c.score or 0.0), reverse=True)
        return kept[: self.config.target_keepers]


@dataclass
class AgentResult:
    """Final output: the selection, full per-image state, the trace, and metrics."""

    selected: list[str]
    candidates: list[Candidate]
    steps: list[AgentStep]
    metrics: dict[str, Any]
