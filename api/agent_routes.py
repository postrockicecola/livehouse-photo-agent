"""Conversational agent API: the Gallery copilot.

``POST /api/agent/chat`` runs one user turn through a :class:`ConversationalAgent` bound
to a session's gallery data (read-only skills) with safety guardrails. Tool calls and
guardrail triggers are returned alongside the reply so the UI can render the plumbing
(which tools ran, with what args, and whether a guardrail fired) — the point of the demo.

Conversation memory is **persisted** per browser session in :mod:`services.agent.store`
(``owner = anon:<session_id>``). The chat model + skills are built per request so new
analyses and the active previews dir are always reflected.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.agent import store
from services.agent.context_governance import working_memory_prompt_block
from services.agent.conversation import ConversationMemory
from services.agent.guardrails import GuardrailEvent, Guardrails
from services.agent.runner import AgentRunner, AgentSession, RunnerConfig
from services.agent.skills import agent_workspace_root, safe_session_id
from services.agent.skills.artifacts import sanitize_artifact_name
from services.agent.skills.experience import register_experience_skills
from services.agent.skills.gallery import gallery_registry
from services.agent.skills.knowledge_search import KnowledgeSearchSkill
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
    # Optional UI focus for per-photo skills (recommend_film_for_photo).
    focus_file: Optional[str] = Field(default=None, max_length=500)
    selected_files: Optional[list[str]] = Field(default=None, max_length=200)


class ChatResponse(BaseModel):
    reply: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    guardrail_events: list[dict[str, Any]] = Field(default_factory=list)
    memory_turns: int = 0
    base_dir: str = ""
    error: Optional[str] = None
    # Per-turn observability: backend / rule_id / rounds / grounding / parse_fail.
    trace: dict[str, Any] = Field(default_factory=dict)
    # Structured final answer (summary + files + ui actions); prose is ``reply``.
    final_answer: Optional[dict[str, Any]] = None


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
        {
            "name": s["function"]["name"],
            "description": s["function"]["description"],
            "args": s["function"]["parameters"].get("properties", {}),
            "required": s["function"]["parameters"].get("required", []),
        }
        for s in registry.tool_specs()
    ]
    return json.dumps(tools, ensure_ascii=False)


# Layered system prompt — deterministic shortlist/dedupe/sort/quality routing lives in
# ``services.agent.intent_router`` (code), not in SEMANTIC_HINTS.
PROTOCOL_PROMPT = (
    "You are the Gallery copilot for a livehouse photography curation app. You help the "
    "user search, select, grade, and export one shooting session's analyzed photos, and "
    "search historical sessions through archive_search.\n\n"
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
    "Keep answers concise. When a PHOTO REFS index is present in the final-answer turn, "
    "cite photos only as {ref_0}/{ref_1}/… — never invent or paste raw filenames."
)

SEMANTIC_HINTS = (
    "SEMANTIC TOOL HINTS (fuzzy intents — code already handles 选出N张/初选/交片/朋友圈/"
    "最炸/高潮瞬间/剔糊/连拍去重/按分数排序/发Ins social shortlist):\n"
    "- 找出吉他手/鼓手/全景舞台/逆光/前排/慢门长曝光/有孤独感/宁静忧郁… → "
    '{"tool":"gallery_search","args":{"query":"<paste the user message>","limit":10}} '
    "(tag/caption synonyms hybrid-merged with CLIP text→image, then score re-rank; "
    "慢门 uses RAW ExposureTime EXIF — never CLIP; cite metadata.files / why; "
    "say 已在预览页打开. gallery_select only if they want 初选/导出)\n"
    "- 其它场次/历史照片/整个档案/去年拍的… → "
    '{"tool":"archive_search","args":{"query":"<semantic subject/style>","limit":10,"mode":"hybrid"}} '
    "(cross-session read-only retrieval; do not call gallery_select/export_selected on archive IDs)\n"
    "- 平台规范/摄影手册/内部流程/公司知识… → "
    '{"tool":"knowledge_search","args":{"query":"<paste the knowledge question>","limit":5,"mode":"hybrid"}} '
    "(answer only from metadata.chunks and cite source_ref labels)\n"
    "- 最炸的吉他手 / 高潮+主体 → recipe (energy/peak) + query in the same gallery_search\n"
    "- energy 最高 → gallery_search with sort_by=\"energy\", limit=10\n"
    "- 技术高构图一般 → mark_score_gap\n"
    "- 记住我的偏好 / 以后少选剪影 → remember_preference(key, value)\n"
    "- 对选片明确评价（太暗/太糊/没张力/这张很好）→ "
    "record_selection_feedback with the referenced selection_history files, query, "
    "accepted/rejected decision, and reason_code\n"
    "- 想按我过去的取舍选 / 参考以前偏好 → retrieve_selection_experience\n"
    "- 最适合这张 / 自动推荐胶片感 / 帮我选胶片 → "
    '{"tool":"recommend_film_for_photo","args":{"prompt":"<paste the user message>"}} '
    "(uses analysis tags for the focus photo; pass file/focus_file when known. "
    "Code also routes this intent. UI 「打开风格预览」.)\n"
    "- 复古胶片 / Cinestill / 黑白纪实 / 修成…风格看看 / 颜色再浓烈一些 / 胶片感更狠 → "
    '{"tool":"apply_film_vibe","args":{"prompt":"<paste the user message>"}} '
    "(MUST call this tool — never claim the style was applied from prose alone. "
    "Relative intensify keeps the current film_variant and raises intensity. "
    "UI shows 「打开风格预览」; final answer = tool output only (1–2 short Chinese sentences). "
    "NEVER dump Markdown image lists or enumerate filenames.)\n"
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
    knowledge_dir = (
        os.environ.get("LIVEHOUSE_KNOWLEDGE_DIR")
        or str(Path(__file__).resolve().parents[1] / "data" / "knowledge")
    )
    reg.register(
        KnowledgeSearchSkill(
            knowledge_dir,
            owner=owner,
            tenant=os.environ.get("LIVEHOUSE_AGENT_TENANT") or "default",
        )
    )
    register_experience_skills(
        reg,
        owner=owner,
        tenant=os.environ.get("LIVEHOUSE_AGENT_TENANT") or "default",
    )
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


def _turn_context_from_request(req: ChatRequest, *, base_dir: str = "") -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    if base_dir:
        ctx["base_dir"] = base_dir
    focus = (req.focus_file or "").strip()
    if focus:
        ctx["focus_file"] = focus
    selected = [str(x).strip() for x in (req.selected_files or []) if str(x or "").strip()]
    if selected:
        ctx["selected_files"] = selected[:50]
    return ctx


def _focus_prompt_block(turn_context: dict[str, Any]) -> str:
    if not turn_context:
        return ""
    lines = ["GALLERY FOCUS (from UI — prefer these for recommend_film_for_photo):"]
    if turn_context.get("focus_file"):
        lines.append(f"- focus_file: {turn_context['focus_file']}")
    sel = turn_context.get("selected_files") or []
    if sel:
        lines.append(f"- selected_files: {', '.join(str(x) for x in sel[:12])}")
    return "\n".join(lines)


def _augment_system_prompt(
    base: str,
    owner: str,
    working: Optional[dict[str, Any]] = None,
    turn_context: Optional[dict[str, Any]] = None,
) -> str:
    parts = [base]
    prefs = _load_prefs(owner)
    pref_block = store.preferences_prompt_block(prefs)
    if pref_block:
        parts.append(pref_block)
    wm_block = working_memory_prompt_block(working or {})
    if wm_block:
        parts.append(wm_block)
    focus_block = _focus_prompt_block(turn_context or {})
    if focus_block:
        parts.append(focus_block)
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


def _build_chat_fn(base_dir: str, *, tools: Optional[list[dict[str, Any]]] = None):
    """Build the non-streaming ``ChatFn`` from the shared ``model.*`` config.

    Returns ``(chat_fn, error)`` — exactly one is non-None. Prefers a dedicated
    instruct model (``model.agent_chat_model``) for reliable tool-calling.

    When native tools are enabled for the provider (``LIVEHOUSE_AGENT_NATIVE_TOOLS``
    ``auto`` → openai/vllm on, ollama off; or explicit ``1``/``0``) and ``tools``
    is provided, the backend attaches OpenAI-shaped tools.
    """
    _ = base_dir
    try:
        from services.agent.chat_backend import build_chat_fn
        from utils.config_loader import ConfigLoader

        model_cfg = ConfigLoader.get_model_config(ConfigLoader.load())
        chat_model = str(model_cfg.get("agent_chat_model") or "").strip() or None
        # Pass tools through; build_chat_fn decides native vs text from provider + env.
        return build_chat_fn(model_cfg, model_name=chat_model, tools=tools), None
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
def agent_chat_stream(req: ChatRequest) -> StreamingResponse:
    """Server-Sent Events variant of :func:`agent_chat`.

    Emits ``tool_call`` as skills run, then ``token`` chunks as a *typing effect*
    over the already-finalized reply (buffer → ``{ref_N}`` resolve / groundedness →
    chunk). This is not true token-level streaming of the model; TTFB for answer
    text matches non-stream chat. Terminal ``done`` (guardrail events + base_dir)
    or ``error`` follows. History is loaded from / persisted to the session store.
    """
    base_dir = _resolve_base_dir(req.previews_dir)
    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",  # disable nginx/proxy buffering so tokens flush
        "Connection": "keep-alive",
    }

    owner = store.owner_key(None, req.session_id)
    registry, base_system = _build_registry(req.mode, req.session_id, base_dir, owner=owner)
    chat_fn, err = _build_chat_fn(base_dir, tools=registry.tool_specs())
    if err is not None:
        def _err_gen():
            yield _sse({"type": "error", "error": err, "base_dir": base_dir})
        return StreamingResponse(_err_gen(), media_type="text/event-stream", headers=headers)

    # Load history + working memory first so last_files can be injected into the prompt.
    conv_id, memory, working = _load_conversation(owner, req, base_system)
    turn_context = _turn_context_from_request(req, base_dir=base_dir)
    system_prompt = _augment_system_prompt(base_system, owner, working, turn_context)
    memory.system_prompt = system_prompt
    stream_fn = _build_stream_fn(base_dir)
    events: list[GuardrailEvent] = []
    guardrails = Guardrails(on_event=events.append)  # policy: LIVEHOUSE_AGENT_GUARDRAIL_POLICY
    runner = AgentRunner(
        chat_fn=chat_fn,
        skills=registry,
        session=AgentSession(
            owner=owner,
            session_id=req.session_id,
            mode=req.mode,
            base_dir=base_dir,
            conversation_id=conv_id,
            memory=memory,
            tenant=os.environ.get("LIVEHOUSE_AGENT_TENANT") or "default",
            working_memory=working,
            turn_context=turn_context,
        ),
        guardrails=guardrails,
        config=RunnerConfig(max_tool_rounds=_max_rounds(req.mode), wrap_tool_output=False),
        stream_fn=stream_fn,
    )
    agent = runner.agent

    def _gen():
        try:
            for ev in runner.stream(req.message):
                if ev.get("type") == "done":
                    turns = _persist_turn(
                        conv_id,
                        req.message,
                        str(ev.get("reply") or ""),
                        events=list(getattr(agent, "_events", []) or []),
                        guardrail_events=events,
                        working_memory=dict(getattr(agent, "working_memory", {}) or {}),
                    )
                    tr = dict(getattr(agent, "last_trace", {}) or ev.get("trace") or {})
                    ev = {
                        **ev,
                        "base_dir": base_dir,
                        "memory_turns": turns,
                        "trace": tr,
                        "final_answer": getattr(agent, "last_final_answer", None) or ev.get("final_answer"),
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
def agent_chat(req: ChatRequest) -> ChatResponse:
    base_dir = _resolve_base_dir(req.previews_dir)

    owner = store.owner_key(None, req.session_id)
    registry, base_system = _build_registry(req.mode, req.session_id, base_dir, owner=owner)
    chat_fn, err = _build_chat_fn(base_dir, tools=registry.tool_specs())
    if err is not None:
        return ChatResponse(reply="", base_dir=base_dir, error=err)

    conv_id, memory, working = _load_conversation(owner, req, base_system)
    turn_context = _turn_context_from_request(req, base_dir=base_dir)
    system_prompt = _augment_system_prompt(base_system, owner, working, turn_context)
    memory.system_prompt = system_prompt
    events: list[GuardrailEvent] = []
    guardrails = Guardrails(on_event=events.append)
    # Gallery skills read our own DB → trusted; don't fence their output as untrusted
    # (the fence hurts weaker chat models). Injection scanning still runs for observability.
    runner = AgentRunner(
        chat_fn=chat_fn,
        skills=registry,
        session=AgentSession(
            owner=owner,
            session_id=req.session_id,
            mode=req.mode,
            base_dir=base_dir,
            conversation_id=conv_id,
            memory=memory,
            tenant=os.environ.get("LIVEHOUSE_AGENT_TENANT") or "default",
            working_memory=working,
            turn_context=turn_context,
        ),
        guardrails=guardrails,
        config=RunnerConfig(max_tool_rounds=_max_rounds(req.mode), wrap_tool_output=False),
    )

    try:
        result = runner.chat(req.message)
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
        final_answer=result.final_answer,
        guardrail_events=[
            {"kind": e.kind, "triggered": e.triggered, "matches": e.matches, "detail": e.detail}
            for e in events if e.triggered
        ],
        memory_turns=turns,
        base_dir=base_dir,
        trace=dict(getattr(result, "trace", None) or {}),
    )


def _tool_calls_by_assistant_index(events: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Map chronological ``done`` events → tool_calls lists for successive assistant turns."""
    out: list[list[dict[str, Any]]] = []
    for ev in events or []:
        if str(ev.get("type") or "") != "done":
            continue
        raw = ev.get("tool_calls")
        if not isinstance(raw, list):
            out.append([])
            continue
        cleaned: list[dict[str, Any]] = []
        for tc in raw:
            if isinstance(tc, dict) and tc.get("tool"):
                cleaned.append(tc)
        out.append(cleaned)
    return out


@router.get("/api/agent/history")
def agent_history(
    session_id: str,
    mode: str = "gallery",
) -> dict[str, Any]:
    """Return the persisted user/assistant transcript for this browser session/mode.

    Assistant rows include ``tool_calls`` reconstructed from persisted done events so
    ChatDock CTAs (e.g. 「打开风格预览」) survive hydrate / reload.
    """
    owner = store.owner_key(None, session_id)
    conn = store.store_connect()
    try:
        conv_id = store.get_or_create_conversation(conn, owner, session_id, mode)
        msgs = store.load_messages(conn, conv_id)
        prefs = store.get_preferences(conn, owner)
        events = store.load_agent_events(conn, conv_id, limit=500)
    finally:
        conn.close()
    tool_batches = _tool_calls_by_assistant_index(events)
    assistant_i = 0
    messages: list[dict[str, Any]] = []
    for m in msgs:
        row: dict[str, Any] = {"role": m["role"], "content": m["content"]}
        if m["role"] == "assistant":
            if assistant_i < len(tool_batches) and tool_batches[assistant_i]:
                row["tool_calls"] = tool_batches[assistant_i]
            assistant_i += 1
        messages.append(row)
    return {
        "messages": messages,
        "memory_turns": len(msgs),
        "preferences": prefs,
    }


@router.get("/api/agent/trace")
def agent_trace(
    session_id: str,
    mode: str = "gallery",
    limit: int = 100,
) -> dict[str, Any]:
    """Replay tool-call / done events for this conversation (step-level observability)."""
    owner = store.owner_key(None, session_id)
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
