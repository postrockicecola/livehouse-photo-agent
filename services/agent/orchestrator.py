"""Multi-agent orchestration: a coordinator that fans a task out to sub-agents.

The single :class:`~services.agent.loop.CurationAgent` runs one ReAct loop over one
candidate set under one budget. This module is the layer above it — the "SuperAgent" /
coordinator pattern the harness JD asks for:

    coordinator
      ├─ route()      decompose the candidate set into disjoint sub-tasks
      ├─ spawn        one CurationAgent per sub-task, each with an ISOLATED state and
      │               its OWN slice of the global inference budget (budget propagation)
      ├─ run          sub-agents run independently (optionally concurrently)
      └─ merge        union the sub-selections, re-rank globally, cap to target_keepers

Why this shape (the interview points):

- **Context isolation.** Each sub-agent only ever sees its own shard, so its planner
  prompt stays small and the shards can't cross-talk or confuse a weak model. This is
  the multi-agent analog of the single loop's "small idx, lean prompt" context work.
- **Budget propagation.** The global ``max_inferences`` is split across shards before
  any sub-agent runs, so the fan-out can never exceed the parent budget — the same
  "never run away" guarantee the single loop gives, lifted one level up.
- **Observability that composes.** Every sub-agent already emits per-step events and a
  metrics dict; the coordinator tags each with an ``agent_id`` and aggregates them into
  one multi-agent trace + a roll-up metrics surface, so the Infra Console timeline can
  render a parent→child tree instead of a flat loop.

Nothing here imports the heavy pipeline or a model server: sub-agents are built by an
injected factory, exactly like ``tools``/``planner`` are injected into the single loop,
so the whole orchestrator is unit-tested with scripted fakes.
"""
from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from services.agent.loop import CurationAgent
from services.agent.messaging import ROLE_SPECIALIST, MessageBus, build_handoff_messages
from services.agent.types import (
    AgentConfig,
    AgentResult,
    AgentState,
    AgentStep,
    Candidate,
)

logger = logging.getLogger(__name__)


@dataclass
class SubTask:
    """One shard of work handed to a sub-agent: its candidates and its own budget.

    ``config`` is a *child* :class:`AgentConfig` whose ``max_inferences`` /
    ``max_analyze_candidates`` are the coordinator's split of the global budget, so the
    sub-agent physically cannot spend more than its allocation.
    """

    agent_id: str
    label: str
    candidates: list[Candidate]
    config: AgentConfig


# Builds the CurationAgent for one shard. Injected (like tools/planner on the single
# loop) so production wires real tools and tests pass a scripted fake.
SubAgentFactory = Callable[[SubTask], CurationAgent]

# Multi-agent step hook: same idea as the loop's StepHook but carries the agent_id so a
# sink (e.g. job_events) can attribute every step to its sub-agent for a parent→child
# timeline. ``agent_id == "coordinator"`` is used for the merge/finalize step.
OrchestratorStepHook = Callable[[str, AgentStep, AgentState], None]


class Router(Protocol):
    """Decomposes the candidate set into disjoint sub-tasks with split budgets."""

    def route(self, candidates: list[Candidate], config: AgentConfig) -> list[SubTask]: ...


def split_budget(config: AgentConfig, num_shards: int, weights: Optional[list[int]] = None) -> list[AgentConfig]:
    """Split the global inference budget across ``num_shards`` child configs.

    ``max_inferences`` and ``max_analyze_candidates`` are partitioned (so the sum of the
    children never exceeds the parent), while ``target_keepers`` is kept at the parent
    value per shard — each shard may *propose* up to that many keepers and the global
    merge caps the union back down to the parent target.
    """
    if num_shards <= 0:
        raise ValueError("num_shards must be >= 1")
    if weights is None:
        weights = [1] * num_shards
    if len(weights) != num_shards or any(w <= 0 for w in weights):
        raise ValueError("weights must be one positive int per shard")
    total_w = sum(weights)

    def _partition(total: int) -> list[int]:
        # Largest-remainder apportionment so the parts sum to exactly ``total`` and each
        # shard gets at least 1 (a shard with 0 budget could never make progress).
        raw = [total * w / total_w for w in weights]
        floors = [max(1, int(x)) for x in raw]
        leftover = total - sum(floors)
        # Hand any remaining units to the shards with the largest fractional parts.
        order = sorted(range(num_shards), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        i = 0
        while leftover > 0 and order:
            floors[order[i % num_shards]] += 1
            leftover -= 1
            i += 1
        return floors

    inf_parts = _partition(config.max_inferences)
    analyze_parts = _partition(config.max_analyze_candidates)
    return [
        dataclasses.replace(
            config,
            max_inferences=inf_parts[s],
            max_analyze_candidates=analyze_parts[s],
        )
        for s in range(num_shards)
    ]


class ShardRouter:
    """Default router: split candidates into ``num_shards`` even shards by count.

    ``strategy="contiguous"`` keeps neighbours together (good when input order is
    meaningful, e.g. a burst sequence); ``"round_robin"`` interleaves (good for evening
    out hard/easy photos across shards). Empty shards are dropped so the budget split
    only covers shards that actually have work.
    """

    def __init__(self, *, num_shards: int = 2, strategy: str = "contiguous") -> None:
        if num_shards < 1:
            raise ValueError("num_shards must be >= 1")
        if strategy not in ("contiguous", "round_robin"):
            raise ValueError(f"unknown strategy {strategy!r}")
        self._num_shards = num_shards
        self._strategy = strategy

    def route(self, candidates: list[Candidate], config: AgentConfig) -> list[SubTask]:
        n = min(self._num_shards, max(1, len(candidates)))
        buckets: list[list[Candidate]] = [[] for _ in range(n)]
        if self._strategy == "round_robin":
            for i, c in enumerate(candidates):
                buckets[i % n].append(c)
        else:  # contiguous
            per = (len(candidates) + n - 1) // n  # ceil
            for i in range(n):
                buckets[i] = candidates[i * per : (i + 1) * per]
        buckets = [b for b in buckets if b]
        child_configs = split_budget(config, len(buckets))
        return [
            SubTask(agent_id=f"sub-{i}", label=f"shard {i} ({len(b)} imgs)", candidates=b, config=cfg)
            for i, (b, cfg) in enumerate(zip(buckets, child_configs))
        ]


class KeyedRouter:
    """Group candidates by a key function (e.g. session / burst / camera) into shards.

    This is the "route by meaning, not by count" variant: each distinct key becomes one
    sub-agent, so a sub-agent reasons over a coherent group. The budget is split evenly
    across the resulting groups.
    """

    def __init__(self, key_fn: Callable[[Candidate], str], *, max_shards: Optional[int] = None) -> None:
        self._key_fn = key_fn
        self._max_shards = max_shards

    def route(self, candidates: list[Candidate], config: AgentConfig) -> list[SubTask]:
        groups: dict[str, list[Candidate]] = {}
        for c in candidates:
            groups.setdefault(str(self._key_fn(c)), []).append(c)
        keys = list(groups)
        if self._max_shards is not None and len(keys) > self._max_shards:
            # Too many keys → fall back to even sharding so the budget split stays sane.
            return ShardRouter(num_shards=self._max_shards).route(candidates, config)
        child_configs = split_budget(config, len(keys))
        return [
            SubTask(agent_id=f"key:{k}", label=f"group {k} ({len(groups[k])} imgs)", candidates=groups[k], config=cfg)
            for k, cfg in zip(keys, child_configs)
        ]


@dataclass
class SubAgentRun:
    """One sub-agent's outcome, kept alongside its id/label for the multi-agent trace."""

    agent_id: str
    label: str
    result: AgentResult


@dataclass
class OrchestrationResult:
    """Coordinator output. Mirrors :class:`AgentResult` (selected/candidates/steps/metrics)
    so it is a drop-in for callers that consume a single agent run, and additionally
    exposes the per-sub-agent breakdown."""

    selected: list[str]
    candidates: list[Candidate]
    steps: list[AgentStep]
    metrics: dict[str, Any]
    subagents: list[SubAgentRun] = field(default_factory=list)


class Coordinator:
    """Runs a task across sub-agents and merges their results (the SuperAgent loop)."""

    def __init__(
        self,
        *,
        subagent_factory: SubAgentFactory,
        config: AgentConfig,
        router: Optional[Router] = None,
        step_hook: Optional[OrchestratorStepHook] = None,
        max_workers: int = 1,
    ) -> None:
        self._factory = subagent_factory
        self._config = config
        self._router = router or ShardRouter(num_shards=2)
        self._step_hook = step_hook
        self._max_workers = max(1, max_workers)

    def run(self, candidates: list[Candidate]) -> OrchestrationResult:
        tasks = self._router.route(list(candidates), self._config)
        if not tasks:
            return OrchestrationResult(selected=[], candidates=[], steps=[], metrics=self._aggregate([]), subagents=[])

        runs = self._run_tasks(tasks)

        merged_candidates: list[Candidate] = []
        merged_steps: list[AgentStep] = []
        for r in runs:
            merged_candidates.extend(r.result.candidates)
            merged_steps.extend(r.result.steps)

        selected = self._merge_selection(runs)
        metrics = self._aggregate(runs)
        metrics["selected_count"] = len(selected)
        return OrchestrationResult(
            selected=selected,
            candidates=merged_candidates,
            steps=merged_steps,
            metrics=metrics,
            subagents=runs,
        )

    def _run_tasks(self, tasks: list[SubTask]) -> list[SubAgentRun]:
        if self._max_workers == 1 or len(tasks) == 1:
            return [self._run_one(t) for t in tasks]
        # Concurrent fan-out: sub-agents are independent (disjoint shards, own budgets),
        # so they parallelize cleanly. Results are re-sorted into task order so the merge
        # and metrics stay deterministic regardless of completion order.
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(tasks))) as pool:
            indexed = list(pool.map(lambda it: (it[0], self._run_one(it[1])), enumerate(tasks)))
        indexed.sort(key=lambda x: x[0])
        return [run for _, run in indexed]

    def _run_one(self, task: SubTask) -> SubAgentRun:
        agent = self._factory(task)
        # Attribute this sub-agent's steps to its agent_id on the shared observability sink.
        if self._step_hook is not None:
            agent._step_hook = self._wrap_step_hook(task.agent_id, agent._step_hook)
        result = agent.run(task.candidates)
        return SubAgentRun(agent_id=task.agent_id, label=task.label, result=result)

    def _wrap_step_hook(self, agent_id: str, inner: Optional[Callable[[AgentStep, AgentState], None]]):
        outer = self._step_hook

        def hook(step: AgentStep, state: AgentState) -> None:
            if inner is not None:
                try:
                    inner(step, state)
                except Exception:
                    logger.exception("inner step_hook failed for %s", agent_id)
            if outer is not None:
                try:
                    outer(agent_id, step, state)
                except Exception:
                    logger.exception("orchestrator step_hook failed for %s", agent_id)

        return hook

    def _merge_selection(self, runs: list[SubAgentRun]) -> list[str]:
        """Union each sub-agent's keepers, then globally re-rank and cap to target.

        Sub-agents finalize their own shard; the coordinator enforces the *global*
        keeper budget so N shards proposing N×target keepers collapse back to one
        target-sized, score-ordered delivery set.
        """
        score_by_id: dict[str, float] = {}
        picked: list[str] = []
        for r in runs:
            by_id = {c.image_id: c for c in r.result.candidates}
            for cid in r.result.selected:
                if cid in score_by_id:
                    continue
                cand = by_id.get(cid)
                score_by_id[cid] = (cand.score if cand and cand.score is not None else float("-inf"))
                picked.append(cid)
        picked.sort(key=lambda cid: score_by_id[cid], reverse=True)
        return picked[: self._config.target_keepers]

    def _aggregate(self, runs: list[SubAgentRun]) -> dict[str, Any]:
        """Roll sub-agent metrics into one multi-agent surface (sums + recomputed rates)."""
        agg: dict[str, Any] = {
            "orchestrator": True,
            "num_subagents": len(runs),
            "max_inferences": self._config.max_inferences,
            "target_keepers": self._config.target_keepers,
            "steps": 0,
            "inferences_used": 0,
            "escalations": 0,
            "llm_fallback_calls": 0,
            "candidates_total": 0,
            "candidates_analyzed": 0,
        }
        llm_steps = 0
        llm_total = 0
        action_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        subagents_meta: list[dict[str, Any]] = []
        for r in runs:
            m = r.result.metrics or {}
            agg["steps"] += int(m.get("steps") or 0)
            agg["inferences_used"] += int(m.get("inferences_used") or 0)
            agg["escalations"] += int(m.get("escalations") or 0)
            agg["llm_fallback_calls"] += int(m.get("llm_fallback_calls") or 0)
            agg["candidates_total"] += int(m.get("candidates_total") or 0)
            agg["candidates_analyzed"] += int(m.get("candidates_analyzed") or 0)
            for k, v in (m.get("action_counts") or {}).items():
                action_counts[k] = action_counts.get(k, 0) + int(v)
            for k, v in (m.get("planner_source_counts") or {}).items():
                source_counts[k] = source_counts.get(k, 0) + int(v)
            subagents_meta.append(
                {
                    "agent_id": r.agent_id,
                    "label": r.label,
                    "selected_count": len(r.result.selected),
                    "inferences_used": m.get("inferences_used"),
                    "max_inferences": m.get("max_inferences"),
                    "steps": m.get("steps"),
                    "escalations": m.get("escalations"),
                    "llm_decision_rate": m.get("llm_decision_rate"),
                }
            )
        llm_steps = source_counts.get("llm", 0)
        llm_total = llm_steps + agg["llm_fallback_calls"]
        agg["llm_decision_rate"] = (llm_steps / llm_total) if llm_total else None
        agg["action_counts"] = action_counts
        agg["planner_source_counts"] = source_counts
        agg["budget_exhausted"] = agg["inferences_used"] >= self._config.max_inferences
        agg["subagents"] = subagents_meta
        return agg


class HandoffCoordinator:
    """Two-tier multi-agent with real agent-to-agent handoff over a :class:`MessageBus`.

    Topology: a tier of cheap **worker** agents (fast tier, escalation disabled) triages
    the whole set in parallel; each worker hands off the candidates it analyzed but is
    *not confident about* — as ``handoff`` messages on the bus — to a single **specialist**
    agent that re-analyzes only those at the full tier. The final selection merges the
    workers' confident keepers with the specialist's verdicts, globally re-ranked.

    This is the difference between "fan-out + merge" (:class:`Coordinator`) and a genuine
    multi-agent system: agents communicate through explicit, observable messages and a
    role-based route, and compute is spent where a worker *asked for help* rather than
    uniformly. The global inference budget is partitioned between the worker tier and a
    reserved specialist allocation, so the whole thing still cannot run away.
    """

    def __init__(
        self,
        *,
        subagent_factory: SubAgentFactory,
        config: AgentConfig,
        router: Optional[Router] = None,
        step_hook: Optional[OrchestratorStepHook] = None,
        max_workers: int = 1,
        specialist_fraction: float = 0.34,
        bus: Optional[MessageBus] = None,
    ) -> None:
        if not 0.0 < specialist_fraction < 1.0:
            raise ValueError("specialist_fraction must be in (0, 1)")
        self._factory = subagent_factory
        self._config = config
        self._router = router or ShardRouter(num_shards=2)
        self._step_hook = step_hook
        self._max_workers = max(1, max_workers)
        self._specialist_fraction = specialist_fraction
        self._bus = bus or MessageBus()

    @property
    def bus(self) -> MessageBus:
        return self._bus

    def run(self, candidates: list[Candidate]) -> OrchestrationResult:
        # 1) Partition the global budget: reserve a slice for the specialist, the rest
        #    funds the worker tier (which Coordinator then splits across shards).
        spec_budget = max(1, int(round(self._config.max_inferences * self._specialist_fraction)))
        worker_budget = max(1, self._config.max_inferences - spec_budget)
        worker_config = dataclasses.replace(
            self._config, max_inferences=worker_budget, allow_escalation=False, base_tier="fast"
        )

        # 2) Worker tier: cheap fast-tier triage over all candidates (reuse Coordinator).
        worker_coord = Coordinator(
            subagent_factory=self._factory,
            config=worker_config,
            router=self._router,
            step_hook=self._step_hook,
            max_workers=self._max_workers,
        )
        worker_res = worker_coord.run(candidates)

        # 3) Handoff: each worker posts its low-confidence analyses to the bus.
        handoff_eval_cfg = dataclasses.replace(self._config, base_tier="fast", escalation_tier="full")
        handed_off_ids: set[str] = set()
        for sub in worker_res.subagents:
            for msg in build_handoff_messages(sub.result, handoff_eval_cfg, sender=sub.agent_id):
                self._bus.send(msg)
                handed_off_ids.add(msg.payload["image_id"])

        # 4) Specialist tier: drain the role queue and re-analyze the hard ones at full tier.
        specialist_run = self._run_specialist(spec_budget)

        # 5) Merge: full-tier candidate data overrides the fast-tier copy for handed-off ids.
        cand_by_id: dict[str, Candidate] = {c.image_id: c for c in worker_res.candidates}
        if specialist_run is not None:
            for c in specialist_run.result.candidates:
                cand_by_id[c.image_id] = c

        selected = self._merge_selection(worker_res, specialist_run, handed_off_ids, cand_by_id)

        subagents = list(worker_res.subagents)
        steps = list(worker_res.steps)
        if specialist_run is not None:
            subagents.append(specialist_run)
            steps.extend(specialist_run.result.steps)

        metrics = self._build_metrics(worker_res, specialist_run, handed_off_ids, selected)
        return OrchestrationResult(
            selected=selected,
            candidates=list(cand_by_id.values()),
            steps=steps,
            metrics=metrics,
            subagents=subagents,
        )

    def _run_specialist(self, spec_budget: int) -> Optional[SubAgentRun]:
        messages = self._bus.drain(ROLE_SPECIALIST)
        if not messages:
            return None
        # Fresh candidates (no prior analysis) so the specialist analyzes from scratch at
        # the full tier; carry the cheap features so its INSPECT stays free.
        spec_candidates = [
            Candidate(
                image_id=m.payload["image_id"],
                image_path=m.payload["image_path"],
                features=dict(m.payload.get("features") or {}),
            )
            for m in messages
        ]
        spec_config = dataclasses.replace(
            self._config,
            max_inferences=spec_budget,
            max_analyze_candidates=max(self._config.max_analyze_candidates, len(spec_candidates)),
            base_tier="full",
            allow_escalation=False,
        )
        spec_task = SubTask(agent_id=ROLE_SPECIALIST, label=f"specialist ({len(spec_candidates)} handoffs)",
                            candidates=spec_candidates, config=spec_config)
        agent = self._factory(spec_task)
        if self._step_hook is not None:
            inner = agent._step_hook
            outer = self._step_hook

            def hook(step: AgentStep, state: AgentState) -> None:
                if inner is not None:
                    inner(step, state)
                outer(ROLE_SPECIALIST, step, state)

            agent._step_hook = hook
        result = agent.run(spec_candidates)
        return SubAgentRun(agent_id=ROLE_SPECIALIST, label=spec_task.label, result=result)

    def _merge_selection(
        self,
        worker_res: OrchestrationResult,
        specialist_run: Optional[SubAgentRun],
        handed_off_ids: set[str],
        cand_by_id: dict[str, Candidate],
    ) -> list[str]:
        """Workers' confident keepers (excluding handed-off) ∪ specialist keepers, re-ranked."""
        picked: list[str] = []

        def _add(cid: str) -> None:
            if cid not in picked and cid in cand_by_id:
                picked.append(cid)

        for sub in worker_res.subagents:
            for cid in sub.result.selected:
                if cid in handed_off_ids:
                    continue  # the worker punted this one; let the specialist decide it
                _add(cid)
        if specialist_run is not None:
            for cid in specialist_run.result.selected:
                _add(cid)

        def _score(cid: str) -> float:
            c = cand_by_id.get(cid)
            return c.score if c is not None and c.score is not None else float("-inf")

        picked.sort(key=_score, reverse=True)
        return picked[: self._config.target_keepers]

    def _build_metrics(
        self,
        worker_res: OrchestrationResult,
        specialist_run: Optional[SubAgentRun],
        handed_off_ids: set[str],
        selected: list[str],
    ) -> dict[str, Any]:
        spec_metrics = specialist_run.result.metrics if specialist_run is not None else None
        worker_inf = int(worker_res.metrics.get("inferences_used") or 0)
        spec_inf = int((spec_metrics or {}).get("inferences_used") or 0)
        return {
            "handoff": True,
            "max_inferences": self._config.max_inferences,
            "target_keepers": self._config.target_keepers,
            "num_workers": worker_res.metrics.get("num_subagents"),
            "num_subagents": int(worker_res.metrics.get("num_subagents") or 0) + (1 if specialist_run else 0),
            "handoffs": len(handed_off_ids),
            "specialist_ran": specialist_run is not None,
            "specialist_analyzed": (spec_metrics or {}).get("candidates_analyzed", 0),
            "messages": len(self._bus.history()),
            "inferences_used": worker_inf + spec_inf,
            "worker_inferences": worker_inf,
            "specialist_inferences": spec_inf,
            "selected_count": len(selected),
            "worker_metrics": worker_res.metrics,
            "specialist_metrics": spec_metrics,
            "message_log": [m.summary() for m in self._bus.history()],
        }


def default_subagent_factory(
    *,
    tools: Any,
    planner: Any = None,
    reflect_fn: Optional[Callable[..., Any]] = None,
    metrics_hook: Optional[Callable[[dict[str, Any]], None]] = None,
) -> SubAgentFactory:
    """Build a factory that spawns a :class:`CurationAgent` per shard from shared parts.

    ``tools`` and ``planner`` are stateless w.r.t. a run, so they are safely reused
    across sub-agents; only the per-shard :class:`AgentConfig` (with its split budget)
    differs. Production passes the real :class:`ToolRegistry`; tests pass fakes.
    """
    from services.agent.reflection import reflect as default_reflect

    def factory(task: SubTask) -> CurationAgent:
        kwargs: dict[str, Any] = {
            "tools": tools,
            "config": task.config,
            "reflect_fn": reflect_fn or default_reflect,
        }
        if planner is not None:
            kwargs["planner"] = planner
        if metrics_hook is not None:
            kwargs["metrics_hook"] = metrics_hook
        return CurationAgent(**kwargs)

    return factory
