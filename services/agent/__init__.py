"""Gallery conversational agent (skills + LangGraph chat turn).

Photo scoring / selection stays on the fixed Stage1→2→3 pipeline
(``ANALYZE_*`` jobs). This package is the Gallery chat surface only:
decide → act → answer, with deterministic intent routing for common asks.
"""
from __future__ import annotations

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.conversation_graph import (
    GALLERY_CHAT_MAPPING,
    compile_chat_turn_graph,
    run_chat_turn,
)
from services.agent.runner import AgentRunner, AgentSession, RunnerConfig

__all__ = [
    "AgentRunner",
    "AgentSession",
    "ConversationalAgent",
    "ConversationMemory",
    "GALLERY_CHAT_MAPPING",
    "RunnerConfig",
    "compile_chat_turn_graph",
    "run_chat_turn",
]
