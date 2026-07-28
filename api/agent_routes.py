"""Conversational agent API: the Gallery copilot.

``POST /api/agent/chat`` runs one user turn through a :class:`ConversationalAgent` bound
to a session's gallery data (read-only skills) with safety guardrails. Tool calls and
guardrail triggers are returned alongside the reply so the UI can render the plumbing
(which tools ran, with what args, and whether a guardrail fired) — the point of the demo.

Conversation memory is **persisted** per owner in :mod:`services.agent.store`
(``owner = user:<id>`` when logged in, else ``anon:<session_id>``), so history survives a
server restart and is isolated per user. The chat model + skills are built per request so
new analyses and the active previews dir are always reflected.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from api.auth_routes import resolve_user
from services.agent import store
from services.agent.context_governance import working_memory_prompt_block
from services.agent.conversation import ConversationalAgent, ConversationMemory
from services.agent.guardrails import GuardrailEvent, Guardrails
from services.agent.skills import agent_workspace_root, safe_session_id
from services.agent.skills.artifacts import sanitize_artifact_name
from services.agent.skills.gallery import gallery_registry
from services.agent.skills.memory import register_memory_skills

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=4000)
    previews_dir: Optional[str] = None
    reset: bool = False
    # Gallery session copilot (only supported mode).
    mode: str = Field(default="gallery")


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


def _tool_catalog(registry) -> str:
    tools = [
        {"name": s["function"]["name"], "description": s["function"]["description"], "args": s["function"]["parameters"].get("properties", {})}
        for s in registry.tool_specs()
    ]
    return json.dumps(tools, ensure_ascii=False)


# Layered system prompt — deterministic shortlist/dedupe/sort/quality routing lives in
# ``services.agent.intent_router`` (code), not in SEMANTIC_HINTS.
PROTOCOL_PROMPT = (
    "You are the Gallery copilot for a livehouse photography curation app. You help the "
    "user search, select, grade, and export one shooting session's analyzed photos.\n\n"
    "TOOLS: to use a tool, reply with ONLY a single JSON object on its own:\n"
    '{"tool": "<tool_name>", "args": { ... }}\n'
    "You may call tools in sequence within one turn (e.g. gallery_search then "
    "gallery_select). NEVER call the exact same tool+args twice. When finished, answer "
    "in plain natural language (no JSON). Never invent photo data — always get it from a "
    "tool."
)

STYLE_PROMPT = (
    "STYLE: Do NOT narrate plans or say you will search later. Call the tool now, then "
    "give a short final answer. If count=0: report the tool summary honestly "
    "(e.g. metadata.tag_status=not_available, or shutter_stats when style_intent=slow_shutter). "
    "Keep answers concise; cite real file names/scores from tool metadata.files or rows only."
)

SEMANTIC_HINTS = (
    "SEMANTIC TOOL HINTS (fuzzy intents — code already handles 选出N张/初选/交片/朋友圈/"
    "最炸/高潮瞬间/剔糊/连拍去重/按分数排序/发Ins social shortlist):\n"
    "- 找出吉他手/鼓手/全景舞台/逆光/前排/慢门长曝光… → "
    '{"tool":"gallery_search","args":{"query":"<paste the user message>","limit":10}} '
    "(text match on tags/caption/reason; 慢门 uses RAW ExposureTime EXIF; "
    "cite metadata.files; say 已在预览页打开. gallery_select only if they want 初选/导出)\n"
    "- energy 最高 → gallery_search with sort_by=\"energy\", limit=10\n"
    "- 技术高构图一般 → mark_score_gap\n"
    "- 记住我的偏好 / 以后少选剪影 → remember_preference(key, value)\n"
    "- 复古胶片 / Cinestill / 黑白纪实 / 修成…风格看看 → "
    '{"tool":"apply_film_vibe","args":{"prompt":"<paste the user message>"}} '
    "(MUST call this tool — never claim the style was applied from prose alone. "
    "UI shows 「打开风格预览」; say 已应用风格，请点下方「打开风格预览」 only after success. "
    "NEVER dump Markdown image lists or enumerate dozens of filenames.)\n"
    "- 把刚才选出的 / 这些 / 那批 修成…风格 → if WORKING MEMORY has last_files: "
    "gallery_select(files=last_files) then apply_film_vibe(prompt=…). "
    "Do NOT ask for filenames when last_files is present. Final answer 1–2 short sentences.\n"
    "- 导出预览+RAW → export_selected (after selection exists)"
)


def _system_prompt(registry) -> str:
    """Gallery copilot prompt: protocol + style + semantic hints + tool catalog."""
    return (
        f"{PROTOCOL_PROMPT}\n\n{STYLE_PROMPT}\n\n{SEMANTIC_HINTS}\n\n"
        f"AVAILABLE TOOLS:\n{_tool_catalog(registry)}"
    )


def _build_registry(mode: str, session_id: str, base_dir: str, *, owner: str):
    """Return ``(registry, system_prompt)`` for the gallery copilot."""
    _ = (mode, session_id)  # gallery-only today; mode kept for API compatibility
    reg = gallery_registry(base_dir)
    register_memory_skills(
        reg,
        owner=owner,
        persist=lambda k, v: _persist_pref(owner, k, v),
        loader=lambda: _load_prefs(owner),
    )
    return reg, _system_prompt(reg)


def _persist_pref(owner: str, key: str, value: str) -> None:
    conn = store.store_connect()
    try:
        store.set_preference(conn, owner, key, value)
    finally:
        conn.close()


def _load_prefs(owner: str) -> dict[str, str]:
    conn = store.store_connect()
    try:
        return store.get_preferences(conn, owner)
    finally:
        conn.close()


def _augment_system_prompt(base: str, owner: str, working: Optional[dict[str, Any]] = None) -> str:
    parts = [base]
    prefs = _load_prefs(owner)
    pref_block = store.preferences_prompt_block(prefs)
    if pref_block:
        parts.append(pref_block)
    wm_block = working_memory_prompt_block(working or {})
    if wm_block:
        parts.append(wm_block)
    return "\n\n".join(parts)


def _build_memory(system_prompt: str, history: list[dict[str, Any]]) -> ConversationMemory:
    """Rebuild short-term memory from persisted messages (budget trimming still applies)."""
    mem = ConversationMemory(system_prompt=system_prompt, max_tokens=3000)
    for m in history:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "user":
            mem.add_user(content)
        elif role == "assistant":
            mem.add_assistant(content)
        elif role == "tool":
            mem.add_tool_result(m.get("name") or "tool", content)
    return mem


def _load_conversation(owner: str, req: ChatRequest, system_prompt: str):
    """Return ``(conversation_id, ConversationMemory, working_memory)``.

    Honors ``req.reset`` by clearing persisted messages + working memory first.
    Working memory is loaded from the conversation row, with a fallback rebuild from
    recent tool events (so last gallery_search hits survive across HTTP turns).
    """
    conn = store.store_connect()
    try:
        conv_id = store.get_or_create_conversation(conn, owner, req.session_id, req.mode)
        if req.reset:
            store.reset_conversation(conn, owner, req.session_id, req.mode)
            history: list[dict[str, Any]] = []
            working: dict[str, Any] = {}
        else:
            history = store.load_messages(conn, conv_id)
            working = store.get_working_memory(conn, conv_id)
            if not working.get("last_files"):
                events = store.load_agent_events(conn, conv_id, limit=40)
                rebuilt = store.working_memory_from_events(events)
                if rebuilt.get("last_files"):
                    working = rebuilt
    finally:
        conn.close()
    return conv_id, _build_memory(system_prompt, history), working


def _guardrail_events_for_store(raw: list[Any]) -> list[dict[str, Any]]:
    """Serialize triggered guardrails into ``agent_events`` payloads (review-queue signal)."""
    out: list[dict[str, Any]] = []
    for e in raw or []:
        if isinstance(e, GuardrailEvent):
            if not e.triggered:
                continue
            out.append(
                {
                    "type": "guardrail",
                    "kind": e.kind,
                    "triggered": True,
                    "matches": list(e.matches or []),
                    "detail": dict(e.detail or {}),
                }
            )
        elif isinstance(e, dict) and e.get("triggered"):
            out.append(
                {
                    "type": "guardrail",
                    "kind": e.get("kind"),
                    "triggered": True,
                    "matches": list(e.get("matches") or []),
                    "detail": dict(e.get("detail") or {}),
                }
            )
    return out


def _persist_turn(
    conv_id: int,
    user_text: str,
    reply: str,
    *,
    events: Optional[list[dict[str, Any]]] = None,
    guardrail_events: Optional[list[Any]] = None,
    working_memory: Optional[dict[str, Any]] = None,
) -> int:
    """Append the user message + assistant reply; return the total message count."""
    conn = store.store_connect()
    try:
        store.append_messages(conn, conv_id, [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": reply},
        ])
        to_store = list(events or [])
        to_store.extend(_guardrail_events_for_store(list(guardrail_events or [])))
        if to_store:
            store.append_agent_events(conn, conv_id, to_store)
        if working_memory is not None:
            store.set_working_memory(conn, conv_id, working_memory)
        return store.message_count(conn, conv_id)
    finally:
        conn.close()


def _build_chat_fn(base_dir: str):
    """Build the non-streaming ``ChatFn`` from the shared ``model.*`` config.

    Returns ``(chat_fn, error)`` — exactly one is non-None. Prefers a dedicated
    instruct model (``model.agent_chat_model``) for reliable tool-calling.
    """
    try:
        from services.agent.chat_backend import build_chat_fn
        from utils.config_loader import ConfigLoader

        model_cfg = ConfigLoader.get_model_config(ConfigLoader.load())
        chat_model = str(model_cfg.get("agent_chat_model") or "").strip() or None
        return build_chat_fn(model_cfg, model_name=chat_model), None
    except ValueError as exc:
        return None, f"chat model unavailable: {exc} (set model.provider to ollama/vllm/openai)"
    except Exception as exc:
        logger.exception("failed to build chat backend")
        return None, f"chat backend error: {exc}"


def _build_stream_fn(base_dir: str):
    """Best-effort streaming ``StreamChatFn``; ``None`` if it can't be built (the
    agent then falls back to chunking a one-shot completion)."""
    try:
        from services.agent.chat_backend import build_stream_chat_fn
        from utils.config_loader import ConfigLoader

        model_cfg = ConfigLoader.get_model_config(ConfigLoader.load())
        chat_model = str(model_cfg.get("agent_chat_model") or "").strip() or None
        return build_stream_chat_fn(model_cfg, model_name=chat_model)
    except Exception:
        logger.info("streaming chat backend unavailable; using chunked fallback")
        return None


def _max_rounds(mode: str) -> int:
    """Gallery chat stays short-horizon (search → act → answer)."""
    _ = mode
    return 3


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/api/agent/chat/stream")
def agent_chat_stream(
    req: ChatRequest, authorization: Optional[str] = Header(default=None)
) -> StreamingResponse:
    """Server-Sent Events variant of :func:`agent_chat`.

    Streams ``tool_call`` events as skills run and ``token`` events as the final
    answer is generated, then a terminal ``done`` (with guardrail events + base_dir)
    or ``error`` event. History is loaded from / persisted to the per-owner store.
    """
    base_dir = _resolve_base_dir(req.previews_dir)
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",  # disable nginx/proxy buffering so tokens flush
        "Connection": "keep-alive",
    }

    chat_fn, err = _build_chat_fn(base_dir)
    if err is not None:
        def _err_gen():
            yield _sse({"type": "error", "error": err, "base_dir": base_dir})
        return StreamingResponse(_err_gen(), media_type="text/event-stream", headers=headers)

    user = resolve_user(authorization)
    owner = store.owner_key(user, req.session_id)
    registry, base_system = _build_registry(req.mode, req.session_id, base_dir, owner=owner)
    # Load history + working memory first so last_files can be injected into the prompt.
    conv_id, memory, working = _load_conversation(owner, req, base_system)
    system_prompt = _augment_system_prompt(base_system, owner, working)
    memory.system_prompt = system_prompt
    stream_fn = _build_stream_fn(base_dir)
    events: list[GuardrailEvent] = []
    guardrails = Guardrails(on_event=events.append)
    agent = ConversationalAgent(
        chat_fn, memory=memory, skills=registry, guardrails=guardrails,
        wrap_tool_output=False, max_tool_rounds=_max_rounds(req.mode),
        working_memory=working,
    )

    def _gen():
        try:
            for ev in agent.stream_chat(req.message, stream_fn=stream_fn):
                if ev.get("type") == "done":
                    turns = _persist_turn(
                        conv_id,
                        req.message,
                        str(ev.get("reply") or ""),
                        events=list(getattr(agent, "_events", []) or []),
                        guardrail_events=events,
                        working_memory=dict(getattr(agent, "working_memory", {}) or {}),
                    )
                    ev = {
                        **ev,
                        "base_dir": base_dir,
                        "memory_turns": turns,
                        "user": user,
                        "guardrail_events": [
                            {"kind": e.kind, "triggered": e.triggered, "matches": e.matches, "detail": e.detail}
                            for e in events if e.triggered
                        ],
                    }
                yield _sse(ev)
        except Exception as exc:
            logger.exception("agent stream failed")
            yield _sse({"type": "error", "error": f"model call failed: {exc}", "base_dir": base_dir})

    return StreamingResponse(_gen(), media_type="text/event-stream", headers=headers)


@router.post("/api/agent/chat", response_model=ChatResponse)
def agent_chat(req: ChatRequest, authorization: Optional[str] = Header(default=None)) -> ChatResponse:
    base_dir = _resolve_base_dir(req.previews_dir)

    chat_fn, err = _build_chat_fn(base_dir)
    if err is not None:
        return ChatResponse(reply="", base_dir=base_dir, error=err)

    user = resolve_user(authorization)
    owner = store.owner_key(user, req.session_id)
    registry, base_system = _build_registry(req.mode, req.session_id, base_dir, owner=owner)
    conv_id, memory, working = _load_conversation(owner, req, base_system)
    system_prompt = _augment_system_prompt(base_system, owner, working)
    memory.system_prompt = system_prompt
    events: list[GuardrailEvent] = []
    guardrails = Guardrails(on_event=events.append)
    # Gallery skills read our own DB → trusted; don't fence their output as untrusted
    # (the fence hurts weaker chat models). Injection scanning still runs for observability.
    agent = ConversationalAgent(
        chat_fn, memory=memory, skills=registry, guardrails=guardrails,
        wrap_tool_output=False, max_tool_rounds=_max_rounds(req.mode),
        working_memory=working,
    )

    try:
        result = agent.chat(req.message)
    except Exception as exc:
        logger.exception("agent chat failed")
        return ChatResponse(reply="", base_dir=base_dir, memory_turns=memory.turn_count,
                            error=f"model call failed: {exc}")

    turns = _persist_turn(
        conv_id,
        req.message,
        result.reply,
        events=result.events,
        guardrail_events=events,
        working_memory=result.working_memory,
    )
    return ChatResponse(
        reply=result.reply,
        tool_calls=result.tool_calls,
        guardrail_events=[
            {"kind": e.kind, "triggered": e.triggered, "matches": e.matches, "detail": e.detail}
            for e in events if e.triggered
        ],
        memory_turns=turns,
        base_dir=base_dir,
    )


@router.get("/api/agent/history")
def agent_history(
    session_id: str,
    mode: str = "gallery",
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Return the persisted user/assistant transcript for this owner/session/mode.

    Lets the UI restore a conversation after a reload — the visible proof that memory
    is durable and per-user (a different token sees a different transcript).
    """
    user = resolve_user(authorization)
    owner = store.owner_key(user, session_id)
    conn = store.store_connect()
    try:
        conv_id = store.get_or_create_conversation(conn, owner, session_id, mode)
        msgs = store.load_messages(conn, conv_id)
        prefs = store.get_preferences(conn, owner)
    finally:
        conn.close()
    return {
        "messages": [{"role": m["role"], "content": m["content"]} for m in msgs],
        "memory_turns": len(msgs),
        "preferences": prefs,
    }


@router.get("/api/agent/trace")
def agent_trace(
    session_id: str,
    mode: str = "gallery",
    limit: int = 100,
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """Replay tool-call / done events for this conversation (step-level observability)."""
    user = resolve_user(authorization)
    owner = store.owner_key(user, session_id)
    conn = store.store_connect()
    try:
        conv_id = store.get_or_create_conversation(conn, owner, session_id, mode)
        events = store.load_agent_events(conn, conv_id, limit=max(1, min(500, int(limit))))
    finally:
        conn.close()
    return {"session_id": session_id, "mode": mode, "events": events, "count": len(events)}


@router.get("/api/agent/artifacts/{session_id}/{name}")
def agent_artifact(session_id: str, name: str) -> FileResponse:
    """Serve a file under the per-session agent workspace (sanitized path only)."""
    safe = safe_session_id(session_id)
    fname = sanitize_artifact_name(name)
    session_dir = os.path.join(agent_workspace_root(), safe)
    path = os.path.join(session_dir, fname)
    real_root = os.path.realpath(session_dir)
    real_path = os.path.realpath(path)
    if os.path.commonpath([real_path, real_root]) != real_root or not os.path.isfile(real_path):
        raise HTTPException(status_code=404, detail="artifact not found")
    return FileResponse(real_path, filename=fname)
