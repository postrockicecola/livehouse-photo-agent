"""The curation task framed as an RL environment.

This is the bridge between the existing agent and the training loop. A
:class:`CurationEnvironment` holds a fixed pool of candidate photos, the cheap
``fast_score`` features the policy sees, a deterministic analyze *oracle* (the
precomputed Stage3 score — no live model, exactly the eval harness's setup), and
the human ``keep`` labels used to compute reward.

``rollout`` runs one full episode through the real :class:`CurationAgent`/tool loop
with a supplied planner, then attaches a per-pick reward to each ANALYZE decision the
policy recorded:

    reward(pick) = 1.0 if the analyzed photo is a human keeper else 0.0

i.e. "did spending an inference on this photo land on something a human would keep?".
The episode-level headline is ``analyzed_keeper_recall`` — the fraction of all human
keepers the policy chose to analyze before the budget ran out — the same metric
``scripts/eval/eval_agent_selection.py`` reports, so a trained policy is directly
comparable to the heuristic / random / oracle arms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from services.agent.loop import CurationAgent
from services.agent.rl.policy import RLPolicy
from services.agent.tools import AnalyzeTool, FinalizeTool, InspectTool, ToolRegistry
from services.agent.types import AgentConfig, Candidate


@dataclass
class Rollout:
    """The outcome of one episode, ready for a REINFORCE update."""

    decisions: list[Any]                      # list[AnalyzeDecision]
    analyzed: list[str]
    recall: float                             # analyzed_keeper_recall
    selected: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def n_decisions(self) -> int:
        return len(self.decisions)


@dataclass
class CandidateSpec:
    """One candidate photo: an id plus the cheap features a policy may key on.

    ``fast_score`` is accepted as a convenience (the production Stage2 signal); any
    extra cheap features can be passed via ``features`` and referenced by a policy's
    ``feature_keys`` (e.g. a derived ``fast_score_sq`` or an external rank signal).
    """

    image_id: str
    fast_score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    image_path: Optional[str] = None

    def __post_init__(self) -> None:
        # fast_score is always available as a feature; explicit features win on clash.
        merged = {"fast_score": float(self.fast_score)}
        merged.update({k: float(v) for k, v in (self.features or {}).items()})
        self.features = merged
        self.fast_score = merged["fast_score"]


class CurationEnvironment:
    """A fixed-pool, deterministic curation environment over precomputed scores."""

    def __init__(
        self,
        specs: Sequence[CandidateSpec],
        *,
        keepers: set[str],
        oracle_scores: dict[str, float],
    ) -> None:
        self._specs = list(specs)
        self._keepers = set(keepers)
        self._oracle = dict(oracle_scores)
        self._analyze_fn = _make_oracle_analyze_fn(self._oracle)

    @property
    def n_candidates(self) -> int:
        return len(self._specs)

    @property
    def n_keepers(self) -> int:
        return sum(1 for s in self._specs if s.image_id in self._keepers)

    def _build_candidates(self) -> list[Candidate]:
        return [
            Candidate(
                image_id=s.image_id,
                image_path=s.image_path or s.image_id,
                features=dict(s.features),
            )
            for s in self._specs
        ]

    def rollout(
        self,
        policy: RLPolicy,
        config: AgentConfig,
        *,
        greedy: bool = False,
        rng: Optional[np.random.Generator] = None,
    ) -> Rollout:
        """Run one episode; return its trajectory + reward-bearing metrics."""
        candidates = self._build_candidates()
        policy.begin_episode(candidates, rng=rng, greedy=greedy)
        tools = ToolRegistry(
            inspect=InspectTool(),
            analyze=AnalyzeTool(self._analyze_fn, default_tier=config.base_tier),
            finalize=FinalizeTool(),
        )
        agent = CurationAgent(tools=tools, config=config, planner=policy)
        result = agent.run(candidates)

        decisions = policy.take_trajectory()
        for d in decisions:
            d.reward = 1.0 if d.image_id in self._keepers else 0.0

        analyzed = [c.image_id for c in result.candidates if c.attempts > 0]
        recall = len(set(analyzed) & self._keepers) / max(1, self.n_keepers)
        return Rollout(
            decisions=decisions,
            analyzed=analyzed,
            recall=recall,
            selected=list(result.selected),
            metrics=result.metrics,
        )

    # ------------------------------------------------------------- diagnostics

    def static_order_recall(self, ordered_ids: Sequence[str], budget: int) -> float:
        """Recall of a fixed allocation order (for heuristic/random/oracle baselines)."""
        picked = set(list(ordered_ids)[: max(0, budget)])
        return len(picked & self._keepers) / max(1, self.n_keepers)

    def fast_score_desc_ids(self) -> list[str]:
        return [s.image_id for s in sorted(self._specs, key=lambda s: s.fast_score, reverse=True)]


def _make_oracle_analyze_fn(scores: dict[str, float]):
    """Deterministic analyze: return the precomputed Stage3 score for an image."""

    def _fn(image_path: str, tier: str) -> dict[str, Any]:
        f = Path(image_path).name
        return {"score": scores.get(f, scores.get(image_path, 0.0)), "confidence": 0.9, "verdict": "", "dimensions": {}}

    return _fn
