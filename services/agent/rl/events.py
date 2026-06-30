"""Structured sinks for the three RL training phases.

The training loop is deliberately phrased as ``ROLLOUT -> REWARD -> FEEDBACK`` so it
maps onto the same observability surface the rest of the platform uses:

- :class:`LoggingEventSink` emits one structured log line per phase (``trace_id`` +
  ``phase`` + metrics), mirroring the pipeline / inference structured-logging style.
- :class:`JobEventSink` appends each phase as a ``job_events`` row against a real
  ``jobs`` id, so a training run shows up on the Infra Console timeline exactly like
  an analyze job does — this is the bridge that lets an Operator wrap a training run
  as a first-class job later.

Both implement the same :class:`TrainingEventSink` protocol, so the trainer never
knows whether it is writing to a log file or the SSOT.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol, Sequence

logger = logging.getLogger("agent.rl")


class TrainingEventSink(Protocol):
    """Receives the three phase events emitted once per training iteration."""

    def on_rollout(self, iteration: int, payload: dict[str, Any]) -> None: ...

    def on_reward(self, iteration: int, payload: dict[str, Any]) -> None: ...

    def on_feedback(self, iteration: int, payload: dict[str, Any]) -> None: ...


class NullEventSink:
    """Drops every event (used by tests / pure-compute runs)."""

    def on_rollout(self, iteration: int, payload: dict[str, Any]) -> None:  # noqa: D401
        pass

    def on_reward(self, iteration: int, payload: dict[str, Any]) -> None:
        pass

    def on_feedback(self, iteration: int, payload: dict[str, Any]) -> None:
        pass


class LoggingEventSink:
    """Emit each phase as a structured log line (the default, DB-free sink)."""

    def __init__(self, *, trace_id: str = "rl", level: int = logging.INFO) -> None:
        self._trace_id = trace_id
        self._level = level

    def _emit(self, phase: str, iteration: int, payload: dict[str, Any]) -> None:
        fields = " ".join(f"{k}={_fmt(v)}" for k, v in payload.items())
        logger.log(self._level, "phase=%s iter=%s trace_id=%s %s", phase, iteration, self._trace_id, fields)

    def on_rollout(self, iteration: int, payload: dict[str, Any]) -> None:
        self._emit("ROLLOUT", iteration, payload)

    def on_reward(self, iteration: int, payload: dict[str, Any]) -> None:
        self._emit("REWARD", iteration, payload)

    def on_feedback(self, iteration: int, payload: dict[str, Any]) -> None:
        self._emit("FEEDBACK", iteration, payload)


class JobEventSink:
    """Append each phase as a ``job_events`` row against an existing ``jobs`` id.

    ``conn`` is any sqlite3 connection whose schema includes ``job_events`` (the SSOT
    brain DB). Phases are written with ``to_status`` set to ``ROLLOUT`` / ``REWARD`` /
    ``FEEDBACK`` so the Infra Console timeline groups them like pipeline stages.
    """

    def __init__(self, conn: Any, *, job_id: int, trace_id: str = "rl") -> None:
        self._conn = conn
        self._job_id = job_id
        self._trace_id = trace_id

    def _append(self, phase: str, message: str, payload: dict[str, Any]) -> None:
        from utils.luma_brain import append_job_event

        body = dict(payload)
        body["trace_id"] = self._trace_id
        try:
            append_job_event(self._conn, job_id=self._job_id, to_status=phase, message=message, payload=body)
            self._conn.commit()
        except Exception:
            logger.exception("failed to write RL job_event phase=%s job=%s", phase, self._job_id)

    def on_rollout(self, iteration: int, payload: dict[str, Any]) -> None:
        msg = f"rollout iter={iteration} episodes={payload.get('episodes')} recall={payload.get('recall_mean')}"
        self._append("ROLLOUT", msg, payload)

    def on_reward(self, iteration: int, payload: dict[str, Any]) -> None:
        msg = f"reward iter={iteration} mean={payload.get('reward_mean')} baseline={payload.get('baseline')}"
        self._append("REWARD", msg, payload)

    def on_feedback(self, iteration: int, payload: dict[str, Any]) -> None:
        msg = f"feedback iter={iteration} grad_norm={payload.get('grad_norm')} weights={payload.get('weights')}"
        self._append("FEEDBACK", msg, payload)


class MultiEventSink:
    """Fan out every phase to several sinks (e.g. logging + job_events)."""

    def __init__(self, sinks: Sequence[TrainingEventSink]) -> None:
        self._sinks = list(sinks)

    def on_rollout(self, iteration: int, payload: dict[str, Any]) -> None:
        for s in self._sinks:
            s.on_rollout(iteration, payload)

    def on_reward(self, iteration: int, payload: dict[str, Any]) -> None:
        for s in self._sinks:
            s.on_reward(iteration, payload)

    def on_feedback(self, iteration: int, payload: dict[str, Any]) -> None:
        for s in self._sinks:
            s.on_feedback(iteration, payload)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.4f}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_fmt(x) for x in v) + "]"
    return str(v)
