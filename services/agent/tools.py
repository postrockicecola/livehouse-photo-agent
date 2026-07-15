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
import re
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
# Injected gallery/memory lookup: image_id -> a prior committed analysis dict (at least
# {score, confidence?}) or None. Lets the agent *recall* a score instead of re-analyzing.
GalleryProvider = Callable[[str], Optional[Mapping[str, Any]]]


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


def _score_signal(cand: Candidate) -> tuple[float, str]:
    """Best available quality signal for a candidate: analyzed score > cheap fast_score."""
    if cand.analyzed and cand.score is not None:
        return float(cand.score), "score"
    return cand.fast_score(), "fast_score"


class CompareTool:
    """Zero-cost relative judgement between two candidates (tie-break helper).

    Uses each candidate's best available signal (a real analyzed ``score`` if present,
    otherwise the cheap ``fast_score``). Costs no inference — it only reads state — so
    the planner can weigh two close candidates before deciding which one is worth a VLM
    call, or which of a near-duplicate pair to keep.
    """

    name = "compare"

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        a_id = str(call.args.get("a_id") or "")
        b_id = str(call.args.get("b_id") or "")
        ca = state.candidates.get(a_id)
        cb = state.candidates.get(b_id)
        if ca is None or cb is None or a_id == b_id:
            return ToolResult(ok=False, error="compare needs two distinct known image_ids")
        sa, basis_a = _score_signal(ca)
        sb, basis_b = _score_signal(cb)
        winner = a_id if sa >= sb else b_id
        return ToolResult(
            ok=True,
            observation={
                "a": a_id,
                "b": b_id,
                "a_signal": round(sa, 2),
                "b_signal": round(sb, 2),
                "basis": basis_a if basis_a == basis_b else f"{basis_a}/{basis_b}",
                "winner": winner,
                "margin": round(abs(sa - sb), 2),
            },
        )


_TRAILING_NUM = re.compile(r"(\d+)(?!.*\d)")


def _burst_key(image_id: str) -> Optional[int]:
    """Trailing integer in a filename (e.g. ``DSC02513`` -> 2513); None if absent."""
    m = _TRAILING_NUM.search(image_id)
    return int(m.group(1)) if m else None


class ClusterTool:
    """Zero-cost: group burst / near-duplicate frames so the agent can analyze one
    representative per group instead of every near-identical frame — a real budget win
    for concert photography, where cameras fire long bursts of the same moment.

    Grouping is by trailing frame number proximity (``window`` consecutive shots).
    Annotates each candidate's ``features`` with ``cluster_id`` and ``cluster_rep`` so
    the planner can see the grouping on the next step. Idempotent: sets ``state.clustered``.
    """

    name = "cluster"

    def __init__(self, *, window: int = 3) -> None:
        self._window = max(1, int(window))

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        cands = state.ordered_candidates()
        numbered = sorted(
            (c for c in cands if _burst_key(c.image_id) is not None),
            key=lambda c: (_burst_key(c.image_id), c.image_id),
        )
        unnumbered = [c for c in cands if _burst_key(c.image_id) is None]

        clusters: list[list[Candidate]] = []
        prev_num: Optional[int] = None
        for c in numbered:
            n = _burst_key(c.image_id)
            if prev_num is None or (n - prev_num) > self._window:
                clusters.append([c])
            else:
                clusters[-1].append(c)
            prev_num = n
        clusters.extend([c] for c in unnumbered)  # singletons keep their own group

        multi = 0
        largest = 0
        for cid, members in enumerate(clusters):
            rep = max(members, key=lambda c: _score_signal(c)[0])
            largest = max(largest, len(members))
            if len(members) > 1:
                multi += 1
            for c in members:
                c.features["cluster_id"] = cid
                c.features["cluster_rep"] = c.image_id == rep.image_id

        state.clustered = True
        return ToolResult(
            ok=True,
            observation={
                "clusters": len(clusters),
                "multi_member_clusters": multi,
                "largest_cluster": largest,
                "window": self._window,
            },
        )


class QueryGalleryTool:
    """Zero-cost: recall a prior committed score for one image from the gallery/memory.

    On a hit the candidate is marked analyzed from the recalled result (tier ``recall``,
    no inference spent) so the planner sees the score next step and can keep it without
    a VLM call — reusing prior work instead of paying for it again. Default provider
    always misses, so the tool is safe to register unconditionally.
    """

    name = "query_gallery"

    def __init__(self, provider: Optional[GalleryProvider] = None) -> None:
        self._provider = provider

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        cand = _require_candidate(state, call)
        if cand is None:
            return ToolResult(ok=False, error="unknown or missing image_id")
        cand.features["gallery_queried"] = True
        prior: Optional[Mapping[str, Any]] = None
        if self._provider is not None:
            try:
                prior = self._provider(cand.image_id)
            except Exception as exc:  # a recall miss must never crash the loop
                return ToolResult(ok=False, error=f"gallery_provider failed: {exc}")
        score = _coerce_float((prior or {}).get("score")) if prior else None
        if not prior or score is None:
            return ToolResult(ok=True, observation={"image_id": cand.image_id, "found": False})
        cand.analysis = {**dict(prior), "recalled": True}
        cand.score = score
        cand.confidence = _coerce_float(prior.get("confidence"))
        cand.tier = str(prior.get("tier") or "recall")
        return ToolResult(
            ok=True,
            observation={
                "image_id": cand.image_id,
                "found": True,
                "score": score,
                "tier": cand.tier,
            },
        )


class FinalizeTool:
    """Terminal action: commit the keeper set (explicit list or current keepers).

    When ``processing.diversity_selection.agent_finalize`` is enabled (default), the
    committed set is diversity-capped to at most ``max_per_cluster`` frames per
    visual/burst group, then refilled from other analyzed candidates up to
    ``target_keepers`` so the delivery set covers more of the night.
    """

    name = "finalize"

    def __init__(self, *, diversify: bool | None = None) -> None:
        # None → read config at run time; False disables even if config says on.
        self._diversify = diversify

    def run(self, state: AgentState, call: ToolCall) -> ToolResult:
        explicit = call.args.get("selected")
        if isinstance(explicit, list) and explicit:
            selected = [str(i) for i in explicit if str(i) in state.candidates]
        else:
            selected = [c.image_id for c in state.current_keepers()]

        diversity_meta: dict[str, Any] | None = None
        if self._should_diversify():
            selected, diversity_meta = _diversify_finalize_selection(state, selected)

        state.selected = selected
        state.finalized = True
        obs: dict[str, Any] = {"selected": selected, "count": len(selected)}
        if diversity_meta is not None:
            obs["diversity"] = diversity_meta
        return ToolResult(ok=True, observation=obs)

    def _should_diversify(self) -> bool:
        if self._diversify is False:
            return False
        if self._diversify is True:
            return True
        try:
            from utils.config_loader import ConfigLoader
            from services.diversity_selector import diversity_settings

            return bool(diversity_settings(ConfigLoader.load()).get("finalize_enabled", True))
        except Exception:
            return True


def _candidate_diversity_item(c: Candidate) -> dict[str, Any]:
    dims = None
    if isinstance(c.analysis, dict):
        raw_dims = c.analysis.get("dimensions")
        if isinstance(raw_dims, dict):
            dims = raw_dims
    return {
        "id": c.image_id,
        "path": c.image_path,
        "score": float(c.score or 0.0),
        "dimensions": dims or {},
        "cluster_id": c.features.get("cluster_id"),
    }


def diversify_keeper_selection(
    candidates: Mapping[str, Candidate] | list[Candidate],
    proposed_ids: list[str],
    *,
    target: int,
    fill_ids: list[str] | None = None,
    settings: Mapping[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    """Public helper: diversity-cap a proposed keeper list (also used by orchestrator merge)."""
    from services.diversity_selector import diversify_keeper_ids, diversity_settings

    if settings is None:
        try:
            from utils.config_loader import ConfigLoader

            settings = diversity_settings(ConfigLoader.load())
        except Exception:
            settings = diversity_settings(None)

    if isinstance(candidates, Mapping):
        cand_list = list(candidates.values())
        by_id = candidates
    else:
        cand_list = list(candidates)
        by_id = {c.image_id: c for c in cand_list}

    if not bool(settings.get("finalize_enabled", True)):
        capped = [i for i in proposed_ids if i in by_id][:target]
        return capped, {"signal": "disabled", "before": len(proposed_ids), "after": len(capped), "dropped": []}

    items = [_candidate_diversity_item(c) for c in cand_list]
    if fill_ids is None:
        fill_ids = [c.image_id for c in cand_list if c.image_id not in set(proposed_ids)]
    return diversify_keeper_ids(
        items,
        proposed_ids,
        target=target,
        settings=settings,
        fill_ids=fill_ids,
    )


def _diversify_finalize_selection(
    state: AgentState, selected: list[str]
) -> tuple[list[str], dict[str, Any]]:
    thr = state.config.keep_score_threshold
    fill_ids = [
        c.image_id
        for c in state.ordered_candidates()
        if c.analyzed and (c.score or 0.0) >= thr and c.image_id not in selected
    ]
    fill_ids.sort(
        key=lambda i: float(state.candidates[i].score or 0.0),
        reverse=True,
    )
    return diversify_keeper_selection(
        state.candidates,
        selected,
        target=state.config.target_keepers,
        fill_ids=fill_ids,
    )


class ToolRegistry:
    """Maps :class:`ActionType` to a concrete tool and dispatches calls.

    ``inspect`` / ``analyze`` / ``finalize`` are required. The zero-cost planning tools
    (``compare`` / ``cluster`` / ``query_gallery``) are optional and default-constructed
    when omitted, so existing callers keep working and the LLM planner can always offer
    the richer tool set. Pass a ``query_gallery`` with a real :data:`GalleryProvider` to
    wire recall to a gallery / brain lookup.
    """

    def __init__(
        self,
        *,
        inspect: InspectTool,
        analyze: AnalyzeTool,
        finalize: FinalizeTool,
        compare: Optional[CompareTool] = None,
        cluster: Optional[ClusterTool] = None,
        query_gallery: Optional[QueryGalleryTool] = None,
    ) -> None:
        self._tools: dict[ActionType, Tool] = {
            ActionType.INSPECT: inspect,
            ActionType.ANALYZE: analyze,
            ActionType.COMPARE: compare or CompareTool(),
            ActionType.CLUSTER: cluster or ClusterTool(),
            ActionType.QUERY_GALLERY: query_gallery or QueryGalleryTool(),
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
                "name": "compare",
                "description": "Weigh two candidates against each other (best available signal). No model cost.",
                "args": {"a": "image_id", "b": "image_id"},
            },
            {
                "name": "cluster",
                "description": "Group burst / near-duplicate frames once; analyze one representative per group. No model cost.",
                "args": {},
            },
            {
                "name": "query_gallery",
                "description": "Recall a prior committed score for one image instead of re-analyzing it. No model cost.",
                "args": {"image_id": "string"},
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
