"""Minimal RL-style training loop on top of the curation agent.

The package turns the existing one-shot curation agent into a closed feedback loop —
``ROLLOUT -> REWARD -> FEEDBACK`` — so a policy that decides *which* photos to spend a
limited analyze budget on is improved from its own rollouts. It reuses the real agent
loop, tools, and (optionally) the SSOT ``job_events`` observability surface; only the
learnable policy and the REINFORCE update are new.

See ``scripts/rl/train_curation_policy.py`` for the runnable entry point.
"""
from services.agent.rl.environment import CandidateSpec, CurationEnvironment, Rollout
from services.agent.rl.events import (
    JobEventSink,
    LoggingEventSink,
    MultiEventSink,
    NullEventSink,
    TrainingEventSink,
)
from services.agent.rl.policy import AnalyzeDecision, LinearSoftmaxPolicy, RLPolicy
from services.agent.rl.reinforce import (
    IterationRecord,
    REINFORCETrainer,
    TrainConfig,
    TrainResult,
)

__all__ = [
    "CandidateSpec",
    "CurationEnvironment",
    "Rollout",
    "LinearSoftmaxPolicy",
    "AnalyzeDecision",
    "RLPolicy",
    "REINFORCETrainer",
    "TrainConfig",
    "TrainResult",
    "IterationRecord",
    "TrainingEventSink",
    "LoggingEventSink",
    "JobEventSink",
    "MultiEventSink",
    "NullEventSink",
]
