"""Thin Agent / Runner / Session boundaries for Gallery chat.

Keeps :class:`ConversationalAgent` as the turn engine while giving API code a
stable façade for load → run → persist without growing ``agent_routes``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from services.agent.conversation import (
    ChatFn,
    ConversationalAgent,
    ConversationMemory,
    StreamChatFn,
    TurnResult,
)
from services.agent.guardrails import Guardrails
from services.agent.skills.base import SkillRegistry


@dataclass
class AgentSession:
    """One browser/gallery conversation binding."""

    owner: str
    session_id: str
    mode: str
    base_dir: str
    conversation_id: int
    memory: ConversationMemory
    working_memory: dict[str, Any] = field(default_factory=dict)
    turn_context: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerConfig:
    max_tool_rounds: int = 3
    wrap_tool_output: bool = False


class AgentRunner:
    """Build a :class:`ConversationalAgent` for one turn and expose chat/stream."""

    def __init__(
        self,
        *,
        chat_fn: ChatFn,
        skills: SkillRegistry,
        session: AgentSession,
        guardrails: Optional[Guardrails] = None,
        config: Optional[RunnerConfig] = None,
        stream_fn: Optional[StreamChatFn] = None,
    ) -> None:
        self.session = session
        self.stream_fn = stream_fn
        cfg = config or RunnerConfig()
        ctx = dict(session.turn_context or {})
        ctx.setdefault("base_dir", session.base_dir)
        self.agent = ConversationalAgent(
            chat_fn,
            memory=session.memory,
            skills=skills,
            guardrails=guardrails,
            wrap_tool_output=cfg.wrap_tool_output,
            max_tool_rounds=cfg.max_tool_rounds,
            working_memory=session.working_memory,
            turn_context=ctx,
        )

    def chat(self, message: str) -> TurnResult:
        return self.agent.chat(message)

    def stream(self, message: str) -> Iterator[dict[str, Any]]:
        return self.agent.stream_chat(message, stream_fn=self.stream_fn)
