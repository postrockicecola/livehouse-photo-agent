"""Agent-to-agent messaging: a small, observable bus for handoffs between agents.

The orchestrator's :class:`~services.agent.orchestrator.Coordinator` fans work out to
*independent* sub-agents. This module adds the missing half of a real multi-agent
system — **agents talking to each other** — without coupling the agent loop to any
transport:

- :class:`AgentMessage` is one typed message (sender → recipient, a ``kind`` and a
  payload). A ``recipient`` is an agent id or a *role* (e.g. ``"specialist"``).
- :class:`MessageBus` is a thread-safe in-memory bus: ``send`` enqueues per-recipient
  and appends to an append-only ``history`` (the observability surface — every message
  can be replayed into ``job_events``), ``drain`` pulls a recipient's pending messages.
- :func:`build_handoff_messages` is the handoff *policy*: it reuses the loop's own
  :func:`~services.agent.reflection.reflect` verdict to decide which candidates an agent
  analyzed but is *not confident about*, and turns each into a ``handoff`` message for a
  stronger agent. This is the "I did the cheap pass, you take the hard ones" protocol —
  triage → specialist — expressed as explicit messages rather than an in-process call.

The bus is deliberately tiny and dependency-free so it is fully unit-tested and could be
swapped for Redis/NATS behind the same ``send`` / ``drain`` shape.
"""
from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

# Well-known role recipients (a message may target a role instead of a concrete id).
ROLE_SPECIALIST = "specialist"


@dataclass
class AgentMessage:
    """One message on the bus. ``seq`` is assigned by the bus on send (0 until then)."""

    sender: str
    recipient: str
    kind: str  # "handoff" | "result" | "note"
    payload: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def summary(self) -> str:
        """Compact one-liner for a timeline / job_event."""
        img = self.payload.get("image_id")
        tail = f" image={img}" if img else ""
        return f"#{self.seq} {self.sender}->{self.recipient} [{self.kind}]{tail}"


class MessageBus:
    """Thread-safe in-memory message bus with per-recipient queues + full history."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, list[AgentMessage]] = defaultdict(list)
        self._history: list[AgentMessage] = []
        self._seq = 0

    def send(self, message: AgentMessage) -> AgentMessage:
        """Enqueue a message for its recipient and record it in history (assigns ``seq``)."""
        with self._lock:
            self._seq += 1
            message.seq = self._seq
            self._queues[message.recipient].append(message)
            self._history.append(message)
        return message

    def drain(self, recipient: str) -> list[AgentMessage]:
        """Atomically remove and return all pending messages for ``recipient``."""
        with self._lock:
            msgs = self._queues.get(recipient, [])
            self._queues[recipient] = []
            return msgs

    def pending(self, recipient: str) -> int:
        with self._lock:
            return len(self._queues.get(recipient, []))

    def history(self) -> list[AgentMessage]:
        """All messages ever sent, in order (append-only; for observability/replay)."""
        with self._lock:
            return list(self._history)


def build_handoff_messages(
    result: Any,
    config: Any,
    *,
    sender: str,
    recipient: str = ROLE_SPECIALIST,
) -> list[AgentMessage]:
    """Turn an agent's low-confidence analyses into handoff messages for a stronger agent.

    Reuses :func:`~services.agent.reflection.reflect` (with escalation enabled) as the
    handoff *policy*: any candidate the sender actually analyzed (``attempts > 0``) whose
    verdict says "escalate" — low confidence, ambiguous-band score, degraded/invalid
    output — is handed off. The payload carries everything the specialist needs to
    re-analyze from scratch at a higher tier (path + cheap features + why it was punted).
    """
    import dataclasses

    from services.agent.reflection import reflect

    # Evaluate handoff-worthiness as if escalation were allowed, regardless of the
    # worker's own (escalation-disabled) config.
    eval_config = dataclasses.replace(config, allow_escalation=True)

    messages: list[AgentMessage] = []
    for cand in getattr(result, "candidates", []):
        if getattr(cand, "attempts", 0) <= 0:
            continue  # the worker never touched it; not a handoff, just unfinished work
        verdict = reflect(cand, eval_config)
        if not verdict.escalate:
            continue
        messages.append(
            AgentMessage(
                sender=sender,
                recipient=recipient,
                kind="handoff",
                payload={
                    "image_id": cand.image_id,
                    "image_path": cand.image_path,
                    "features": dict(cand.features),
                    "reason": verdict.reason,
                    "fast_tier_score": cand.score,
                    "fast_tier_confidence": cand.confidence,
                },
            )
        )
    return messages
