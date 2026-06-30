"""REINFORCE trainer for the curation allocation policy.

Each iteration runs the loop the JD cares about, phrased as three explicit phases:

    ROLLOUT   collect a batch of episodes from CurationEnvironment (data collection /
              environment interaction)
    REWARD    score each ANALYZE pick (keeper or not) and update the baseline
              (reward evaluation)
    FEEDBACK  apply the policy-gradient update to the policy weights
              (training feedback)

The estimator is REINFORCE with a moving-average baseline; because each ANALYZE pick
gets its own immediate keeper/not-keeper reward, this is the contextual-bandit form of
policy gradient — which is exactly what "choose the next item to analyze" is:

    w  <-  w + lr * mean_over_picks[ (reward - baseline) * (phi_chosen - E_p[phi]) ]

Nothing here touches a GPU or a live model: rewards come from the deterministic oracle
+ human labels, so a full training run is reproducible on a laptop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from services.agent.rl.environment import CurationEnvironment, Rollout
from services.agent.rl.events import NullEventSink, TrainingEventSink
from services.agent.rl.policy import LinearSoftmaxPolicy
from services.agent.types import AgentConfig


@dataclass
class TrainConfig:
    iterations: int = 60
    batch: int = 12
    lr: float = 0.3
    baseline_decay: float = 0.9
    eval_every: int = 5
    seed: int = 20260626


@dataclass
class IterationRecord:
    iteration: int
    reward_mean: float
    recall_mean: float
    baseline: float
    grad_norm: float
    weights: list[float]
    greedy_recall: Optional[float] = None


@dataclass
class TrainResult:
    history: list[IterationRecord]
    final_weights: list[float]
    init_greedy_recall: float
    final_greedy_recall: float
    feature_keys: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_keys": self.feature_keys,
            "init_greedy_recall": round(self.init_greedy_recall, 4),
            "final_greedy_recall": round(self.final_greedy_recall, 4),
            "final_weights": [round(w, 4) for w in self.final_weights],
            "history": [
                {
                    "iteration": r.iteration,
                    "reward_mean": round(r.reward_mean, 4),
                    "recall_mean": round(r.recall_mean, 4),
                    "baseline": round(r.baseline, 4),
                    "grad_norm": round(r.grad_norm, 5),
                    "weights": [round(w, 4) for w in r.weights],
                    "greedy_recall": (round(r.greedy_recall, 4) if r.greedy_recall is not None else None),
                }
                for r in self.history
            ],
        }


class REINFORCETrainer:
    def __init__(
        self,
        env: CurationEnvironment,
        policy: LinearSoftmaxPolicy,
        agent_config: AgentConfig,
        *,
        sink: Optional[TrainingEventSink] = None,
    ) -> None:
        self._env = env
        self._policy = policy
        self._agent_config = agent_config
        self._sink = sink or NullEventSink()

    def train(self, cfg: TrainConfig) -> TrainResult:
        rng = np.random.default_rng(cfg.seed)
        baseline: Optional[float] = None
        history: list[IterationRecord] = []

        init_greedy = self._env.rollout(self._policy, self._agent_config, greedy=True).recall

        for it in range(1, cfg.iterations + 1):
            # --- ROLLOUT: collect a batch of episodes -------------------------
            rollouts: list[Rollout] = [
                self._env.rollout(self._policy, self._agent_config, greedy=False, rng=rng)
                for _ in range(cfg.batch)
            ]
            recall_mean = float(np.mean([r.recall for r in rollouts])) if rollouts else 0.0
            self._sink.on_rollout(
                it,
                {
                    "episodes": len(rollouts),
                    "recall_mean": recall_mean,
                    "picks": sum(r.n_decisions for r in rollouts),
                },
            )

            # --- REWARD: score every pick, update the baseline ----------------
            rewards = [d.reward for r in rollouts for d in r.decisions]
            reward_mean = float(np.mean(rewards)) if rewards else 0.0
            baseline = reward_mean if baseline is None else (
                cfg.baseline_decay * baseline + (1.0 - cfg.baseline_decay) * reward_mean
            )
            self._sink.on_reward(
                it,
                {"reward_mean": reward_mean, "baseline": baseline, "picks": len(rewards)},
            )

            # --- FEEDBACK: REINFORCE policy-gradient step ---------------------
            grad = np.zeros_like(self._policy.w)
            n = 0
            for r in rollouts:
                for d in r.decisions:
                    grad = grad + (d.reward - baseline) * d.grad_log_prob()
                    n += 1
            if n > 0:
                grad = grad / n
            self._policy.w = self._policy.w + cfg.lr * grad
            grad_norm = float(np.linalg.norm(grad))
            weights = [float(x) for x in self._policy.w]
            self._sink.on_feedback(
                it, {"grad_norm": grad_norm, "weights": weights, "baseline": baseline}
            )

            greedy_recall: Optional[float] = None
            if it == 1 or it == cfg.iterations or (cfg.eval_every and it % cfg.eval_every == 0):
                greedy_recall = self._env.rollout(self._policy, self._agent_config, greedy=True).recall

            history.append(
                IterationRecord(
                    iteration=it,
                    reward_mean=reward_mean,
                    recall_mean=recall_mean,
                    baseline=float(baseline),
                    grad_norm=grad_norm,
                    weights=weights,
                    greedy_recall=greedy_recall,
                )
            )

        final_greedy = self._env.rollout(self._policy, self._agent_config, greedy=True).recall
        return TrainResult(
            history=history,
            final_weights=[float(x) for x in self._policy.w],
            init_greedy_recall=init_greedy,
            final_greedy_recall=final_greedy,
            feature_keys=list(self._policy.feature_keys),
        )
