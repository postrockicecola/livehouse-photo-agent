"""Tools the curation agent can call, plus a registry that dispatches by action.

Design notes
------------
- Tools are the *only* way the agent touches the outside world. Each tool is a
  callable ``(state, call) -> ToolResult`` so the loop stays pure and testable.
- ``InspectTool`` and ``FinalizeTool`` cost no model calls; ``AnalyzeTool`` costs
  exactly one VLM inference and is where the budget (and A's cost story) is spent.
- ``AnalyzeTool`` takes an injected ``analyze_fn(image_path, tier) -> dict``. Tests
  pass a deterministic fake; production passes :func:`build_stage3_analyze_fn`,
  which rides the existing ``inference`` layer / Stage3 prompt + parser — i.e. the
  agent reuses the real serving infra rather than a parallel model path.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Mapping, Optional, Protocol

from services.agent.types import (
    ActionType,
    AgentState,
    Candidate,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Injected analysis backend: given an image path and a model tier ("fast"/"full"),
# return a Stage3-like dict with at least {score, confidence, dimensions, verdict}.
AnalyzeFn = Callable[[str, str], dict[str, Any]]
# Injected cheap feature source: image_id -> {tech_score, fast_score, blur_type, ...}.
FeatureProvider = Callable[[str], Mapping[str, Any]]


class Tool(Protocol):
    name: str

    def run(self, state: AgentState, call: ToolCall) -> ToolResult: ...


class InspectTool:
    """Pull cheap Stage1/2 features for one candidate (no model call)."""

    name = "inspect"

    def __init__(self, feature_provider: Optional[FeatureProvider] = None) -> None:
        self._features = feature_provider

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        cand = _require_candidate(state, call)
        if cand is None:
            return ToolResult(ok=False, error="unknown or missing image_id")
        feats: dict[str, Any] = dict(cand.features)
        if self._features is not None:
            try:
                feats.update(dict(self._features(cand.image_id) or {}))
            except Exception as exc:  # tool errors must not crash the loop
                return ToolResult(ok=False, error=f"feature_provider failed: {exc}")
        cand.features = feats
        cand.inspected = True
        return ToolResult(
            ok=True,
            observation={
                "image_id": cand.image_id,
                "tech_score": feats.get("tech_score"),
                "fast_score": feats.get("fast_score"),
                "blur_type": feats.get("blur_type"),
            },
        )


class AnalyzeTool:
    """Run one VLM analysis (fast or full tier) on a candidate; costs 1 inference."""

    name = "analyze"

    def __init__(self, analyze_fn: AnalyzeFn, *, default_tier: str = "fast") -> None:
        self._analyze = analyze_fn
        self._default_tier = default_tier

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        cand = _require_candidate(state, call)
        if cand is None:
            return ToolResult(ok=False, error="unknown or missing image_id")
        tier = str(call.args.get("tier") or self._default_tier)
        t0 = time.perf_counter()
        try:
            raw = self._analyze(cand.image_path, tier)
        except Exception as exc:
            cand.attempts += 1
            return ToolResult(
                ok=False,
                error=f"analyze_fn failed: {exc}",
                inference_cost=1,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        result = dict(raw or {})
        cand.attempts += 1
        cand.tier = tier
        cand.analysis = result
        cand.score = _coerce_float(result.get("score"))
        cand.confidence = _coerce_float(result.get("confidence"))
        if tier == state.config.escalation_tier and call.source == "reflection":
            cand.escalated = True
        return ToolResult(
            ok=not result.get("error"),
            observation={
                "image_id": cand.image_id,
                "tier": tier,
                "score": cand.score,
                "confidence": cand.confidence,
                "verdict": result.get("verdict"),
                "error": result.get("error"),
            },
            error=str(result.get("reason")) if result.get("error") else None,
            inference_cost=1,
            latency_ms=latency_ms,
        )


class FinalizeTool:
    """Terminal action: commit the keeper set (explicit list or current keepers)."""

    name = "finalize"

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        explicit = call.args.get("selected")
        if isinstance(explicit, list) and explicit:
            selected = [str(i) for i in explicit if str(i) in state.candidates]
        else:
            selected = [c.image_id for c in state.current_keepers()]
        state.selected = selected
        state.finalized = True
        return ToolResult(
            ok=True,
            observation={"selected": selected, "count": len(selected)},
        )


class ToolRegistry:
    """Maps :class:`ActionType` to a concrete tool and dispatches calls."""

    def __init__(self, *, inspect: InspectTool, analyze: AnalyzeTool, finalize: FinalizeTool) -> None:
        self._tools: dict[ActionType, Tool] = {
            ActionType.INSPECT: inspect,
            ActionType.ANALYZE: analyze,
            ActionType.FINALIZE: finalize,
        }

    def dispatch(self, state: AgentState, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.action)
        if tool is None:
            return ToolResult(ok=False, error=f"no tool for action {call.action!r}")
        return tool.run(state, call)

    @property
    def tool_schema(self) -> list[dict[str, Any]]:
        """Machine-readable description handed to an LLM planner for tool-calling."""
        return [
            {
                "name": "inspect",
                "description": "Pull cheap Stage1/2 features (tech_score, fast_score, blur_type) for one image. No model cost.",
                "args": {"image_id": "string"},
            },
            {
                "name": "analyze",
                "description": "Run one VLM analysis on one image. Costs 1 inference. Use tier='full' to escalate.",
                "args": {"image_id": "string", "tier": "fast|full"},
            },
            {
                "name": "finalize",
                "description": "Stop and commit the final keeper selection.",
                "args": {"selected": "optional list of image_id"},
            },
        ]


def _require_candidate(state: AgentState, call: ToolCall) -> Optional[Candidate]:
    if not call.image_id:
        return None
    return state.candidates.get(call.image_id)


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_stage3_analyze_fn(
    *,
    config_path: str,
    source_dir: str,
    trace_id: str = "agent",
    job_id: int | None = None,
    worker_id: int = 0,
) -> AnalyzeFn:
    """Production analyze backend: reuse the real Stage3 VLM via the inference layer.

    Heavy imports are deferred so importing this module (and running the agent with
    a fake backend in tests) needs no model server, torch, or DB. The returned
    callable maps the agent's tier to the existing fast/full Stage3 dual-mode.
    """
    from services.processor.aesthetic_pipeline import AestheticPipeline
    from services.processor.stages.deep_analysis import analyze_with_dimensions

    pipe = AestheticPipeline(
        config_path=config_path,
        source_dir=source_dir,
        trace_id=trace_id,
        job_id=job_id,
        worker_id=worker_id,
    )

    def _analyze(image_path: str, tier: str) -> dict[str, Any]:
        result = analyze_with_dimensions(
            pipe.vlm,
            pipe.config,
            image_path,
            log_context={
                "trace_id": trace_id,
                "job_id": job_id,
                "worker_id": worker_id,
                "provider": pipe.model_provider,
                "model": pipe.model_name,
            },
        )
        out = dict(result or {})
        sr = out.get("stage3_result")
        if isinstance(sr, dict):
            out.setdefault("score", sr.get("score"))
            out.setdefault("confidence", sr.get("confidence"))
            out.setdefault("dimensions", sr.get("dimensions"))
            out.setdefault("verdict", sr.get("verdict"))
        return out

    return _analyze
