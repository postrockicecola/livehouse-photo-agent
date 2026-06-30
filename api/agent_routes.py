"""Conversational agent API: the Gallery copilot.

``POST /api/agent/chat`` runs one user turn through a :class:`ConversationalAgent` bound
to a session's gallery data (read-only skills) with safety guardrails. Tool calls and
guardrail triggers are returned alongside the reply so the UI can render the plumbing
(which tools ran, with what args, and whether a guardrail fired) — the point of the demo.

Session memory is in-process and keyed by ``session_id`` (v1: cleared on restart; bounded
to avoid unbounded growth). The chat model + skills are built per request so new analyses
and the active previews dir are always reflected.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.guardrails import GuardrailEvent, Guardrails
from services.agent.skills.gallery import gallery_registry

logger = logging.getLogger(__name__)

router = APIRouter()

# Bounded in-process session memory store (session_id -> ConversationMemory).
_MAX_SESSIONS = 200
_sessions: "OrderedDict[str, ConversationMemory]" = OrderedDict()
_sessions_lock = threading.Lock()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    previews_dir: Optional[str] = None
    reset: bool = False


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    guardrail_events: list[dict[str, Any]] = Field(default_factory=list)
    memory_turns: int = 0
    base_dir: str = ""
    error: Optional[str] = None


def _resolve_base_dir(previews_dir: Optional[str]) -> str:
    if previews_dir and previews_dir.strip():
        return previews_dir.strip()
    try:
        from api.gallery_routes import _runtime_base_dir

        return _runtime_base_dir()
    except Exception:
        import os

        return os.getcwd()


def _system_prompt(registry) -> str:
    """Instruct the model on the bounded tool-call protocol + advertise the tools."""
    tools = [
        {"name": s["function"]["name"], "description": s["function"]["description"], "args": s["function"]["parameters"].get("properties", {})}
        for s in registry.tool_specs()
    ]
    return (
        "You are the Gallery copilot for a livehouse photography curation app. You help the "
        "user explore one shooting session's analyzed photos (scores, tags, keep/discard "
        "categories, VLM captions).\n\n"
        "TOOLS: to use a tool, reply with ONLY a single JSON object on its own:\n"
        '{\"tool\": \"<tool_name>\", \"args\": { ... }}\n'
        "Call AT MOST ONE tool per question, and NEVER call the same tool twice. As soon as "
        "a tool result appears above, you MUST answer in plain natural language (no JSON, no "
        "further tool calls). Never invent photo data — always get it from a tool. Keep "
        "answers concise and reference real file names/scores.\n\n"
        f"AVAILABLE TOOLS:\n{json.dumps(tools, ensure_ascii=False)}"
    )


def _get_memory(session_id: str, registry, *, reset: bool) -> ConversationMemory:
    with _sessions_lock:
        if reset:
            _sessions.pop(session_id, None)
        mem = _sessions.get(session_id)
        if mem is None:
            mem = ConversationMemory(system_prompt=_system_prompt(registry), max_tokens=3000)
            _sessions[session_id] = mem
            while len(_sessions) > _MAX_SESSIONS:
                _sessions.popitem(last=False)
        else:
            _sessions.move_to_end(session_id)
        return mem


@router.post("/api/agent/chat", response_model=ChatResponse)
def agent_chat(req: ChatRequest) -> ChatResponse:
    base_dir = _resolve_base_dir(req.previews_dir)
    registry = gallery_registry(base_dir)

    # Build the chat backend from the same model.* config the rest of the app uses,
    # but prefer a dedicated instruct model (model.agent_chat_model) for tool-calling.
    try:
        from services.agent.chat_backend import build_chat_fn
        from utils.config_loader import ConfigLoader

        model_cfg = ConfigLoader.get_model_config(ConfigLoader.load())
        chat_model = str(model_cfg.get("agent_chat_model") or "").strip() or None
        chat_fn = build_chat_fn(model_cfg, model_name=chat_model)
    except ValueError as exc:
        return ChatResponse(reply="", base_dir=base_dir,
                            error=f"chat model unavailable: {exc} (set model.provider to ollama/vllm/openai)")
    except Exception as exc:
        logger.exception("failed to build chat backend")
        return ChatResponse(reply="", base_dir=base_dir, error=f"chat backend error: {exc}")

    events: list[GuardrailEvent] = []
    guardrails = Guardrails(on_event=events.append)
    memory = _get_memory(req.session_id, registry, reset=req.reset)
    # Gallery skills read our own DB → trusted; don't fence their output as untrusted
    # (the fence hurts weaker chat models). Injection scanning still runs for observability.
    agent = ConversationalAgent(
        chat_fn, memory=memory, skills=registry, guardrails=guardrails, wrap_tool_output=False
    )

    try:
        result = agent.chat(req.message)
    except Exception as exc:
        logger.exception("agent chat failed")
        return ChatResponse(reply="", base_dir=base_dir, memory_turns=memory.turn_count,
                            error=f"model call failed: {exc}")

    return ChatResponse(
        reply=result.reply,
        tool_calls=result.tool_calls,
        guardrail_events=[
            {"kind": e.kind, "triggered": e.triggered, "matches": e.matches, "detail": e.detail}
            for e in events if e.triggered
        ],
        memory_turns=memory.turn_count,
        base_dir=base_dir,
    )
