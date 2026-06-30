"""A learnable, stochastic planner for the curation agent.

:class:`LinearSoftmaxPolicy` is a drop-in :class:`~services.agent.planner.Planner`:
the existing :class:`~services.agent.loop.CurationAgent` calls ``next_action(state)``
exactly as it does for the heuristic/LLM planners. The difference is the ANALYZE
decision — *which* un-analyzed candidate to spend the next inference on — is sampled
from a softmax over a linear score of cheap per-candidate features:

    logit_i = (w . phi_i) / temperature      p = softmax(logit)      a ~ p

For every sampled ANALYZE the policy records the action distribution and the feature
matrix of the choice set, which is exactly what REINFORCE needs to compute the
policy-gradient update afterwards (``grad log p_a = phi_a - E_p[phi]``).

INSPECT (cheap, no cost) and FINALIZE are handled deterministically, like the other
planners, so the model only ever decides the budgeted, high-value picks.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, Sequence

import numpy as np

from services.agent.types import ActionType, AgentState, Candidate, ToolCall


class RLPolicy(Protocol):
    """A planner the trainer can drive: the agent's ``Planner`` plus episode hooks."""

    def begin_episode(
        self,
        candidates: Sequence[Candidate],
        *,
        rng: Optional["np.random.Generator"] = None,
        greedy: bool = False,
    ) -> None: ...

    def take_trajectory(self) -> list["AnalyzeDecision"]: ...

    def next_action(self, state: AgentState) -> ToolCall: ...


@dataclass
class AnalyzeDecision:
    """One sampled ANALYZE pick — the unit of credit assignment for REINFORCE."""

    phi: np.ndarray              # (pool_size, n_features) standardized feature matrix
    probs: np.ndarray            # (pool_size,) action distribution at decision time
    chosen: int                  # local index into the choice set
    image_id: str
    reward: float = 0.0          # filled in by the environment after the episode

    def grad_log_prob(self) -> np.ndarray:
        """∂ log p(chosen) / ∂w = phi_chosen - E_p[phi]  (the score function)."""
        expected = self.probs @ self.phi
        return self.phi[self.chosen] - expected


@dataclass
class LinearSoftmaxPolicy:
    """Softmax-over-features allocation policy with explicit, learnable weights."""

    feature_keys: Sequence[str] = ("fast_score",)
    temperature: float = 1.0
    w: np.ndarray = field(default_factory=lambda: np.zeros(1))

    def __post_init__(self) -> None:
        self.feature_keys = tuple(self.feature_keys)
        self.w = np.asarray(self.w, dtype=float).reshape(len(self.feature_keys))
        self._mean = np.zeros(len(self.feature_keys))
        self._std = np.ones(len(self.feature_keys))
        self._rng: Optional[np.random.Generator] = None
        self._greedy = False
        self._traj: list[AnalyzeDecision] = []

    # ------------------------------------------------------------------ episode

    def begin_episode(
        self,
        candidates: Sequence[Candidate],
        *,
        rng: Optional[np.random.Generator] = None,
        greedy: bool = False,
    ) -> None:
        """Reset per-episode state and fix feature standardization for this rollout."""
        mat = self._raw_matrix(candidates)
        if mat.shape[0] > 0:
            self._mean = mat.mean(axis=0)
            std = mat.std(axis=0)
            std[std < 1e-8] = 1.0
            self._std = std
        else:
            self._mean = np.zeros(len(self.feature_keys))
            self._std = np.ones(len(self.feature_keys))
        self._rng = rng
        self._greedy = greedy
        self._traj = []

    def take_trajectory(self) -> list[AnalyzeDecision]:
        """Hand the recorded ANALYZE decisions to the trainer and clear the buffer."""
        traj, self._traj = self._traj, []
        return traj

    # -------------------------------------------------------------- Planner API

    def next_action(self, state: AgentState) -> ToolCall:
        not_inspected = state.not_inspected()
        if not_inspected:
            c = not_inspected[0]
            return ToolCall(action=ActionType.INSPECT, image_id=c.image_id, reason="inspect", source="rl")

        if state.can_analyze_more():
            pool = state.analyzable()
            if pool:
                phi = np.stack([self._phi(c) for c in pool])      # (P, F)
                logits = (phi @ self.w) / max(self.temperature, 1e-6)
                probs = _softmax(logits)
                if self._greedy:
                    chosen = int(np.argmax(logits))
                elif self._rng is not None:
                    chosen = int(self._rng.choice(len(pool), p=probs))
                    self._traj.append(
                        AnalyzeDecision(phi=phi, probs=probs, chosen=chosen, image_id=pool[chosen].image_id)
                    )
                else:
                    chosen = int(np.argmax(probs))
                return ToolCall(
                    action=ActionType.ANALYZE,
                    image_id=pool[chosen].image_id,
                    args={"tier": state.config.base_tier},
                    reason="rl-policy sample",
                    source="rl",
                )

        return ToolCall(action=ActionType.FINALIZE, reason="budget spent; commit keepers", source="rl")

    # ----------------------------------------------------------------- features

    def _raw_matrix(self, candidates: Sequence[Candidate]) -> np.ndarray:
        if not candidates:
            return np.zeros((0, len(self.feature_keys)))
        return np.array(
            [[_feature(c, k) for k in self.feature_keys] for c in candidates],
            dtype=float,
        )

    def _phi(self, cand: Candidate) -> np.ndarray:
        raw = np.array([_feature(cand, k) for k in self.feature_keys], dtype=float)
        return (raw - self._mean) / self._std


def _feature(cand: Candidate, key: str) -> float:
    try:
        return float(cand.features.get(key, 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - np.max(logits)
    e = np.exp(z)
    return e / np.sum(e)
