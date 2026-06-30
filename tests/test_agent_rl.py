"""Tests for the minimal RL training loop (services/agent/rl).

The environment is fully synthetic and deterministic: human keepers are the photos
with the *lowest* fast_score, so a policy initialised to the production heuristic
(prefer-high-fast_score) starts with recall 0 and must learn a negative weight to
recover keepers. This exercises the whole loop — environment rollout, trajectory
recording, per-pick reward, and the REINFORCE update — without any model or GPU.
"""
from __future__ import annotations

import numpy as np

from services.agent.rl import (
    CandidateSpec,
    CurationEnvironment,
    LinearSoftmaxPolicy,
    REINFORCETrainer,
    TrainConfig,
)
from services.agent.rl.policy import _softmax
from services.agent.types import AgentConfig


def _build_env(n: int = 40, n_keepers: int = 10):
    # fast_score = i (0..n-1); keepers are the n_keepers LOWEST fast_score images.
    specs = [CandidateSpec(image_id=f"img{i:02d}", fast_score=float(i)) for i in range(n)]
    keepers = {f"img{i:02d}" for i in range(n_keepers)}
    oracle = {f"img{i:02d}": float(i) for i in range(n)}  # irrelevant to reward here
    return CurationEnvironment(specs, keepers=keepers, oracle_scores=oracle)


def _agent_config(env: CurationEnvironment, budget: int) -> AgentConfig:
    return AgentConfig(
        target_keepers=budget,
        keep_score_threshold=0.0,
        max_inferences=budget,
        max_analyze_candidates=budget,
        allow_escalation=False,
        max_steps=env.n_candidates + budget + 50,
    )


def test_softmax_is_a_distribution():
    p = _softmax(np.array([1.0, 2.0, 3.0]))
    assert abs(float(p.sum()) - 1.0) < 1e-9
    assert np.all(p > 0)


def test_rollout_records_trajectory_and_reward():
    env = _build_env(n=20, n_keepers=5)
    cfg = _agent_config(env, budget=5)
    policy = LinearSoftmaxPolicy(feature_keys=("fast_score",), w=np.array([0.0]))
    rng = np.random.default_rng(0)
    ro = env.rollout(policy, cfg, greedy=False, rng=rng)
    assert ro.n_decisions == 5  # one decision per analyze pick within budget
    assert 0.0 <= ro.recall <= 1.0
    for d in ro.decisions:
        assert d.reward in (0.0, 1.0)
        assert d.phi.shape[1] == 1
        assert abs(float(d.probs.sum()) - 1.0) < 1e-9


def test_policy_learns_to_beat_its_heuristic_init():
    env = _build_env(n=40, n_keepers=10)
    budget = 10
    cfg = _agent_config(env, budget=budget)
    # Init = prefer-high-fast_score (the production heuristic) → analyzes non-keepers.
    policy = LinearSoftmaxPolicy(feature_keys=("fast_score",), w=np.array([1.5]))

    trainer = REINFORCETrainer(env, policy, cfg)
    result = trainer.train(TrainConfig(iterations=60, batch=16, lr=0.5, eval_every=10, seed=7))

    # The heuristic init analyzes the 10 highest fast_score images → zero keepers.
    assert result.init_greedy_recall == 0.0
    # After learning, greedy should prefer LOW fast_score and recover most keepers.
    assert result.final_greedy_recall >= 0.7
    assert result.final_greedy_recall > result.init_greedy_recall
    # The learned weight must have flipped negative (prefer low fast_score).
    assert result.final_weights[0] < 0.0
