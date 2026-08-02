"""Multi-turn conversational agent with memory, context-window management, and tools.

This is the dialogue counterpart to the curation graph: it keeps a running conversation,
trims it to a token budget (oldest turns first, optionally rolled into a running summary),
and on each user turn runs the LangGraph ``decide → act → answer`` subgraph
(:mod:`services.agent.conversation_graph`) so the model can call Agent Skills before the
final reply.

Design choices that keep it testable and provider-agnostic:

- The model is an injected ``ChatFn = (messages) -> str`` so unit tests use a scripted
  fake and production wires it to any ``/v1/chat/completions`` backend.
- Tool use is a bounded, explicit protocol: the model emits a single JSON object
  ``{"tool": name, "args": {...}}`` to call a skill, or plain text to answer. At most
  ``max_tool_rounds`` tool calls run per user turn, so a turn always terminates.
- Memory is the SSOT for context; the agent never silently grows an unbounded prompt.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

from services.agent.context_governance import (
    DEFAULT_TOOL_RESULT_CHARS,
    compress_working_memory,
    truncate_tool_observation,
    working_memory_prompt_block,
)
from services.agent.groundedness import ground_reply
from services.agent.guardrails import Guardrails
from services.agent.intent_router import RouteMatch, route_gallery_intent
from services.agent.skills.base import SkillRegistry
from services.agent.tool_protocol import (
    looks_like_tool_intent,
    parse_tool_call,
    resolve_tool_decision,
)

logger = logging.getLogger(__name__)

# A chat backend: a list of {role, content} messages -> assistant text.
ChatFn = Callable[[list[dict[str, str]]], str]
# A streaming chat backend: same input, yields the assistant text token-by-token.
StreamChatFn = Callable[[list[dict[str, str]]], Iterator[str]]
# Optional summarizer for evicted turns: old messages -> a short summary string.
Summarizer = Callable[[list["Message"]], str]
# Optional observability hook: one structured event per tool / turn boundary.
TurnHook = Callable[[dict[str, Any]], None]


def approx_tokens(text: str) -> int:
    """Cheap, dependency-free token estimate (~4 chars/token). Good enough for budgeting."""
    return max(1, len(text) // 4)


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None  # tool name for role == "tool"

    def as_dict(self) -> dict[str, str]:
        d = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        return d

    def tokens(self) -> int:
        return approx_tokens(self.content) + 4  # small per-message overhead


@dataclass
class ConversationMemory:
    """Bounded conversation history: a pinned system prompt + a trimmed message window.

    When the running token estimate exceeds ``max_tokens``, the oldest non-system turns
    are evicted. If a ``summarizer`` is set, evicted turns are folded into a single
    rolling summary message kept right after the system prompt, so older context is
    compressed rather than lost outright.
    """

    system_prompt: str = ""
    max_tokens: int = 2000
    summarizer: Optional[Summarizer] = None
    _summary: Optional[str] = None
    _turns: list[Message] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self._turns.append(Message("user", text))
        self._enforce_budget()

    def add_assistant(self, text: str) -> None:
        self._turns.append(Message("assistant", text))
        self._enforce_budget()

    def add_assistant_tool_call(self, tool: str, args: dict[str, Any]) -> None:
        """Record the model's tool decision so the next decide sees a complete chain.

        Protocol order is ``assistant({"tool","args"}) → tool(result)``. Skipping the
        assistant turn leaves a bare tool observation with no causal anchor.
        """
        payload = json.dumps({"tool": tool, "args": args or {}}, ensure_ascii=False)
        self.add_assistant(payload)

    def add_tool_result(self, name: str, content: str, *, max_chars: int = DEFAULT_TOOL_RESULT_CHARS) -> None:
        self._turns.append(Message("tool", truncate_tool_observation(content, max_chars=max_chars), name=name))
        self._enforce_budget()

    def _base_messages(self) -> list[Message]:
        base: list[Message] = []
        if self.system_prompt:
            base.append(Message("system", self.system_prompt))
        if self._summary:
            base.append(Message("system", f"Summary of earlier conversation:\n{self._summary}"))
        return base

    def _current_tokens(self) -> int:
        return sum(m.tokens() for m in self._base_messages()) + sum(m.tokens() for m in self._turns)

    def _enforce_budget(self) -> None:
        evicted: list[Message] = []
        # Evict oldest turns until within budget (always keep at least the last turn).
        while self._current_tokens() > self.max_tokens and len(self._turns) > 1:
            evicted.append(self._turns.pop(0))
        if evicted and self.summarizer is not None:
            try:
                prior = [Message("system", self._summary)] if self._summary else []
                self._summary = self.summarizer(prior + evicted)
            except Exception:  # summarization is best-effort; never break the chat
                logger.exception("conversation summarizer failed")

    def messages(self) -> list[dict[str, str]]:
        return [m.as_dict() for m in (self._base_messages() + self._turns)]

    @property
    def summary(self) -> Optional[str]:
        return self._summary

    @property
    def turn_count(self) -> int:
        return len(self._turns)


def _parse_tool_call(text: str) -> Optional[dict[str, Any]]:
    """Extract a ``{"tool": name, "args": {...}}`` object from model output, if present."""
    return parse_tool_call(text)


def _chunk_text(text: str, size: int = 4) -> Iterator[str]:
    """Split already-computed text into small pieces for a typing effect (no re-gen)."""
    for i in range(0, len(text), max(1, size)):
        yield text[i : i + size]


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


# Clean, minimal context used to force a final answer. Weaker chat models (e.g. llava)
# revert to "how can I help?" when the heavy tool-protocol system prompt + role:"tool"
# messages are present, but answer correctly from a lean prompt that inlines the data.
_FINAL_ANSWER_SYSTEM = (
    "You are a concise assistant. Answer the user's question using ONLY the provided tool "
    "results. Do not output JSON and do not mention tools. When metadata includes recipe / "
    "rationale / pick_reasons / why, briefly explain the selection criteria and 1–3 example "
    "photos with their why lines — do not invent scores. When SESSION CONTEXT lists user "
    "preferences or last_files, honor them in wording and framing — still never invent photo data. "
    "For apply_film_vibe / recommend_film_for_photo: reply in 1–2 short Chinese sentences from "
    "metadata.reply_zh or output only — do NOT list filenames."
)
_FINAL_ANSWER_NUDGE = (
    "Using ONLY the tool results already shown above, answer my question now in plain, "
    "natural language. Prefer citing metadata.rationale and a few pick_reasons/why lines "
    "when present. For film-vibe tools, copy metadata.reply_zh (or output) and stop — "
    "do not enumerate files. Do NOT output JSON and do NOT call any more tools."
)
_FILM_GRADE_TOOLS = frozenset({"apply_film_vibe", "recommend_film_for_photo"})
_PREFS_SECTION_PREFIX = "LONG-TERM USER PREFERENCES"


def _prefs_block_from_system(system_prompt: str) -> str:
    """Pull the durable-prefs section out of the full (tool-protocol) system prompt."""
    if not system_prompt or _PREFS_SECTION_PREFIX not in system_prompt:
        return ""
    for chunk in system_prompt.split("\n\n"):
        if chunk.strip().startswith(_PREFS_SECTION_PREFIX):
            return chunk.strip()
    return ""
_NO_ANSWER_FALLBACK = (
    "I gathered the data with the tools above but couldn't compose a final answer this "
    "turn. Please rephrase your question or ask about a specific photo or metric."
)


_LANGGRAPH_REQUIRED = (
    "LangGraph is required for the production chat runtime. "
    "Install langgraph, or set LIVEHOUSE_AGENT_RUNTIME=imperative for the legacy loop."
)


class ConversationalAgent:
    """A stateful, multi-turn chat agent that can call skills mid-turn.

    Production runtime is the LangGraph ``decide → act → answer`` subgraph
    (:mod:`services.agent.conversation_graph`). Set ``LIVEHOUSE_AGENT_RUNTIME=imperative``
    only for legacy / tests — missing LangGraph is a hard error, not a silent fallback.
    """

    def __init__(
        self,
        chat_fn: ChatFn,
        *,
        memory: Optional[ConversationMemory] = None,
        skills: Optional[SkillRegistry] = None,
        guardrails: Optional[Guardrails] = None,
        max_tool_rounds: int = 3,
        wrap_tool_output: bool = True,
        max_tool_result_chars: int = DEFAULT_TOOL_RESULT_CHARS,
        turn_hook: Optional[TurnHook] = None,
        working_memory: Optional[dict[str, Any]] = None,
        turn_context: Optional[dict[str, Any]] = None,
    ) -> None:
        self._chat = chat_fn
        self.memory = memory or ConversationMemory()
        self._skills = skills
        self._guardrails = guardrails
        self._max_tool_rounds = max(0, max_tool_rounds)
        # First-party tool output (e.g. our own gallery DB) should NOT be fenced as
        # external/untrusted — the fence adds noise and confuses weaker models. Set
        # False for trusted skills; injection scanning still runs for observability.
        self._wrap_tool_output = wrap_tool_output
        self._max_tool_result_chars = max(512, int(max_tool_result_chars))
        self._turn_hook = turn_hook
        # Working memory: last tool artifacts for the current dialogue (not durable prefs).
        self.working_memory: dict[str, Any] = dict(working_memory or {})
        # Per-request UI focus (focus_file / selected_files) — not persisted.
        self._turn_context: dict[str, Any] = dict(turn_context or {})
        self._events: list[dict[str, Any]] = []
        self.last_backend: str = "langgraph"
        self.last_trace: dict[str, Any] = {}
        self._turn_parse_fail: bool = False
        self._turn_parse_repaired: bool = False
        self._compiled_graph: Any = None

    def _merge_turn_context_args(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Inject Gallery focus into film skills when the model/router omitted them."""
        out = dict(args or {})
        if tool not in ("recommend_film_for_photo", "apply_film_vibe"):
            return out
        focus = str(self._turn_context.get("focus_file") or "").strip()
        if focus and not str(out.get("file") or "").strip() and not str(out.get("focus_file") or "").strip():
            out["focus_file"] = focus
        selected = self._turn_context.get("selected_files")
        if isinstance(selected, list) and selected and not out.get("selected_files"):
            out["selected_files"] = [str(x) for x in selected if str(x or "").strip()]
        # Recent search/select shortlist — recommend_film_for_photo can pick the top file.
        if tool == "recommend_film_for_photo" and not out.get("last_files"):
            last = self.working_memory.get("last_files")
            if isinstance(last, list) and last:
                out["last_files"] = [str(x) for x in last if str(x or "").strip()]
        return out

    def _reset_turn_state(self) -> None:
        self._events = []
        self._turn_parse_fail = False
        self._turn_parse_repaired = False
        self.last_trace = {}

    def _parse_tool_call_repaired(self, raw: str) -> Optional[dict[str, Any]]:
        """Parse tool JSON; one repair completion when output looks tool-ish but invalid."""
        decision = resolve_tool_decision(self._chat, self.memory.messages(), raw)
        if decision.repaired:
            self._turn_parse_repaired = True
            self._emit(
                {
                    "type": "parse_repair",
                    "ok": decision.call is not None,
                    "tool": (decision.call or {}).get("tool"),
                }
            )
            if decision.call is None:
                self._turn_parse_fail = True
        return decision.call

    def _emit(self, event: dict[str, Any]) -> None:
        self._events.append(event)
        if self._turn_hook is None:
            return
        try:
            self._turn_hook(event)
        except Exception:
            logger.exception("conversation turn_hook failed")

    def _update_working_memory(self, name: str, args: dict[str, Any], result) -> None:
        meta = getattr(result, "metadata", None) or {}
        self.working_memory["last_tool"] = name
        if args.get("query") is not None:
            self.working_memory["last_query"] = args.get("query")
        # Prefer explicit result files; gallery_select exposes selected_keys instead.
        files = meta.get("files") or meta.get("selected_keys") or args.get("files")
        if files:
            self.working_memory["last_files"] = list(files)
        if meta.get("citations"):
            self.working_memory["last_citations"] = list(meta.get("citations") or [])
        self.working_memory = compress_working_memory(self.working_memory)

    def _record_tool_decision(self, tool: str, args: dict[str, Any]) -> None:
        """Persist the model's tool-call JSON as an assistant turn (before dispatch)."""
        self.memory.add_assistant_tool_call(tool, dict(args or {}))

    def _record_tool_observation(self, name: str, result) -> None:
        """Persist a tool observation (after dispatch). Pair with ``_record_tool_decision``."""
        obs = json.dumps(result.to_observation(), ensure_ascii=False)
        if self._guardrails is not None:
            if self._wrap_tool_output:
                obs = self._guardrails.guard_untrusted(obs, source=f"tool:{name}")
            else:
                # Still scan for injection (observability) without the heavy fence.
                self._guardrails.scan_input(obs, source=f"tool:{name}")
        self.memory.add_tool_result(name, obs, max_chars=self._max_tool_result_chars)

    def _record_tool_result(self, name: str, result, *, args: Optional[dict[str, Any]] = None) -> None:
        """Commit ``assistant(tool-call) → tool(result)``. Prefer decision-then-dispatch at call sites."""
        if args is not None:
            self._record_tool_decision(name, dict(args or {}))
        self._record_tool_observation(name, result)

    def _graph_kwargs(self) -> dict[str, Any]:
        return {
            "chat_fn": self._chat,
            "memory": self.memory,
            "skills": self._skills,
            "guardrails": self._guardrails,
            "wrap_tool_output": self._wrap_tool_output,
            "max_tool_result_chars": self._max_tool_result_chars,
            "update_working_memory": self._update_working_memory,
            "record_tool_decision": self._record_tool_decision,
            "record_tool_observation": self._record_tool_observation,
            "finalize": self._finalize,
            "force_final_answer": self._force_final_answer,
            "build_final_answer_messages": self._build_final_answer_messages,
            "parse_tool_call": self._parse_tool_call_repaired,
            "looks_like_tool_intent": looks_like_tool_intent,
            "emit": self._emit,
            "no_answer_fallback": _NO_ANSWER_FALLBACK,
            "merge_tool_args": self._merge_turn_context_args,
        }

    def _imperative_requested(self) -> bool:
        from services.agent.conversation_graph import chat_runtime_preference

        return chat_runtime_preference() == "imperative"

    def _get_compiled_graph(self) -> Any:
        """Compile the decide→act→answer graph once per agent instance."""
        if self._compiled_graph is None:
            from services.agent.conversation_graph import (
                compile_chat_turn_graph,
                langgraph_available,
            )

            if not langgraph_available():
                raise RuntimeError(_LANGGRAPH_REQUIRED)
            self._compiled_graph = compile_chat_turn_graph(**self._graph_kwargs())
        return self._compiled_graph

    def _run_langgraph_turn(self, user_text: str, *, defer_answer: bool = False) -> dict[str, Any]:
        from services.agent.conversation_graph import run_chat_turn

        return dict(
            run_chat_turn(
                user_text=user_text,
                max_tool_rounds=self._max_tool_rounds,
                defer_answer=defer_answer,
                app=self._get_compiled_graph(),
                **self._graph_kwargs(),
            )
        )

    def _dispatch_call(self, tool: str, args: dict[str, Any], *, routed: str | None = None) -> dict[str, Any]:
        """Run one skill, update memory / working memory, emit ``tool_call``."""
        assert self._skills is not None
        args = self._merge_turn_context_args(tool, args)
        # Causal chain: assistant(decision) lands before dispatch so a crash mid-tool
        # still leaves a decide-visible anchor for the next round.
        self._record_tool_decision(tool, args)
        result = self._skills.dispatch(tool, args)
        self._update_working_memory(tool, args, result)
        self._record_tool_observation(tool, result)
        meta = dict(getattr(result, "metadata", None) or {})
        if routed:
            meta["routed"] = routed
        if not result.ok and result.error:
            meta.setdefault("error", result.error)
            meta.setdefault("reply_zh", result.error)
        elif result.ok and result.output and "reply_zh" not in meta:
            meta["reply_zh"] = result.output
        tc = {
            "tool": tool,
            "args": args,
            "ok": result.ok,
            "metadata": meta,
        }
        self._emit({"type": "tool_call", **tc})
        return tc

    def _execute_route_match(self, match: RouteMatch) -> tuple[list[dict[str, Any]], list[str]]:
        """Execute deterministic routed tool calls (no LLM tool-selection round)."""
        tool_calls: list[dict[str, Any]] = []
        observations: list[str] = []
        if self._skills is None:
            return tool_calls, observations

        search_files: list[str] = []
        for call in match.calls:
            tc = self._dispatch_call(call.tool, dict(call.args), routed=match.rule_id)
            tool_calls.append(tc)
            observations.append(
                f"{call.tool} -> {json.dumps({'ok': tc['ok'], 'metadata': tc['metadata']}, ensure_ascii=False)}"
            )
            if call.tool == "gallery_search" and tc.get("ok"):
                search_files = list((tc.get("metadata") or {}).get("files") or [])

        if match.select_after_search and search_files:
            sel_args = {"files": search_files}
            tc = self._dispatch_call("gallery_select", sel_args, routed=match.rule_id)
            tool_calls.append(tc)
            observations.append(
                f"gallery_select -> {json.dumps({'ok': tc['ok'], 'metadata': tc['metadata']}, ensure_ascii=False)}"
            )
        return tool_calls, observations

    def _chat_routed(self, user_text: str, match: RouteMatch) -> TurnResult:
        """Deterministic tools + one LLM prose summary (no tool-call generation)."""
        self.last_backend = f"routed:{match.rule_id}"
        tool_calls, observations = self._execute_route_match(match)
        # Film tools: always use skill Chinese text — never let the model invent a style.
        direct = self._direct_film_grade_reply(tool_calls)
        final = direct if direct else self._force_final_answer(user_text, observations)
        reply = self._finalize(final)
        self._done_event(reply, tool_calls, routed=match.rule_id)
        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            working_memory=dict(self.working_memory),
            events=list(self._events),
            trace=dict(self.last_trace),
        )

    def _stream_chat_routed(
        self,
        user_text: str,
        match: RouteMatch,
        *,
        stream_fn: Optional[StreamChatFn] = None,
    ) -> Iterator[dict[str, Any]]:
        self.last_backend = f"routed:{match.rule_id}"
        tool_calls, observations = self._execute_route_match(match)
        for tc in tool_calls:
            yield {"type": "tool_call", **tc}
        direct = self._direct_film_grade_reply(tool_calls)
        if direct:
            reply = self._finalize(direct)
            for piece in _chunk_text(reply):
                yield {"type": "token", "text": piece}
            yield self._done_event(reply, tool_calls, routed=match.rule_id)
            return
        yield from self._stream_answer(
            self._build_final_answer_messages(user_text, observations), stream_fn, tool_calls
        )

    def chat(self, user_text: str) -> TurnResult:
        """Process one user turn: optional tool calls, then a final assistant reply."""
        self._reset_turn_state()
        if self._guardrails is not None:
            user_text, refuse = self._guardrails.mediate_user_input(user_text)
            if refuse is not None:
                self.memory.add_user(user_text)
                reply = self._finalize(refuse)
                self._done_event(reply, [])
                return TurnResult(
                    reply=reply,
                    tool_calls=[],
                    working_memory=dict(self.working_memory),
                    events=list(self._events),
                    trace=dict(self.last_trace),
                )
        self.memory.add_user(user_text)

        match = route_gallery_intent(user_text) if self._skills is not None else None
        if match is not None:
            return self._chat_routed(user_text, match)

        if self._imperative_requested():
            self.last_backend = "imperative"
            return self._chat_imperative(user_text)

        state = self._run_langgraph_turn(user_text, defer_answer=False)
        self.last_backend = str(state.get("backend") or "langgraph")
        reply = str(state.get("reply") or "")
        tool_calls = list(state.get("tool_calls") or [])
        self._attach_trace_to_done(reply, tool_calls)
        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            working_memory=dict(self.working_memory),
            events=list(self._events),
            trace=dict(self.last_trace),
        )

    def _chat_imperative(self, user_text: str) -> TurnResult:
        tool_calls: list[dict[str, Any]] = []
        observations: list[str] = []
        seen: set[str] = set()

        rounds = self._max_tool_rounds if self._skills is not None else 0
        for _ in range(rounds):
            raw = self._chat(self.memory.messages())
            call = self._parse_tool_call_repaired(raw)
            if call is None:
                if looks_like_tool_intent(raw):
                    self._turn_parse_fail = True
                    reply = self._finalize(_NO_ANSWER_FALLBACK)
                else:
                    reply = self._finalize(raw)
                self._done_event(reply, tool_calls)
                return TurnResult(
                    reply=reply,
                    tool_calls=tool_calls,
                    working_memory=dict(self.working_memory),
                    events=list(self._events),
                    trace=dict(self.last_trace),
                )
            merged = self._merge_turn_context_args(call["tool"], call["args"])
            key = f"{call['tool']}:{json.dumps(merged, sort_keys=True, ensure_ascii=False)}"
            if key in seen:
                break
            seen.add(key)
            tc = self._dispatch_call(call["tool"], merged)
            tool_calls.append(tc)
            observations.append(
                f"{call['tool']} -> {json.dumps({'ok': tc['ok'], 'metadata': tc['metadata']}, ensure_ascii=False)}"
            )

        if not tool_calls:
            final = self._chat(self.memory.messages())
            if _parse_tool_call(final) is not None:
                self._turn_parse_fail = True
                final = _NO_ANSWER_FALLBACK
            reply = self._finalize(final)
            self._done_event(reply, tool_calls)
            return TurnResult(
                reply=reply,
                tool_calls=tool_calls,
                working_memory=dict(self.working_memory),
                events=list(self._events),
                trace=dict(self.last_trace),
            )

        final = self._force_final_answer(user_text, observations)
        reply = self._finalize(final)
        self._done_event(reply, tool_calls)
        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            working_memory=dict(self.working_memory),
            events=list(self._events),
            trace=dict(self.last_trace),
        )

    def stream_chat(
        self, user_text: str, *, stream_fn: Optional[StreamChatFn] = None
    ) -> Iterator[dict[str, Any]]:
        """Process one user turn, yielding events as they happen.

        Event shapes (all dicts with a ``type`` key):

        - ``{"type": "tool_call", "tool", "args", "ok"}`` — a skill just ran
        - ``{"type": "token", "text"}``                    — a piece of the final answer
        - ``{"type": "done", "reply", "tool_calls", "memory_turns"}`` — turn finished

        Tool rounds use the LangGraph chat subgraph when available (``defer_answer``);
        the final answer is streamed afterward so SSE behaviour stays unchanged.
        """
        self._reset_turn_state()
        if self._guardrails is not None:
            user_text, refuse = self._guardrails.mediate_user_input(user_text)
            if refuse is not None:
                self.memory.add_user(user_text)
                reply = self._finalize(refuse)
                yield self._done_event(reply, [])
                return
        self.memory.add_user(user_text)

        match = route_gallery_intent(user_text) if self._skills is not None else None
        if match is not None:
            yield from self._stream_chat_routed(user_text, match, stream_fn=stream_fn)
            return

        if self._imperative_requested():
            self.last_backend = "imperative"
            yield from self._stream_chat_imperative(user_text, stream_fn=stream_fn)
            return

        from services.agent.conversation_graph import langgraph_available

        if not langgraph_available():
            raise RuntimeError(_LANGGRAPH_REQUIRED)
        yield from self._stream_chat_langgraph(user_text, stream_fn=stream_fn)

    def _stream_chat_langgraph(
        self, user_text: str, *, stream_fn: Optional[StreamChatFn] = None
    ) -> Iterator[dict[str, Any]]:
        from services.agent.conversation_graph import iter_chat_turn_updates

        self.last_backend = "langgraph"
        tool_calls: list[dict[str, Any]] = []
        direct: Optional[str] = None
        answer_messages: Optional[list[dict[str, str]]] = None

        for node_name, partial in iter_chat_turn_updates(
            user_text=user_text,
            max_tool_rounds=self._max_tool_rounds,
            defer_answer=True,
            app=self._get_compiled_graph(),
            **self._graph_kwargs(),
        ):
            if node_name == "act":
                tcs = list(partial.get("tool_calls") or [])
                if tcs:
                    # updates mode returns the full list after act; emit only the newest.
                    newest = tcs[-1]
                    if not tool_calls or newest != tool_calls[-1]:
                        tool_calls = tcs
                        yield {"type": "tool_call", **newest}
                else:
                    tool_calls = tcs
            elif node_name == "answer":
                if partial.get("direct_reply") is not None:
                    direct = str(partial.get("direct_reply"))
                if partial.get("answer_messages") is not None:
                    answer_messages = list(partial.get("answer_messages") or [])
                if partial.get("tool_calls") is not None:
                    tool_calls = list(partial.get("tool_calls") or [])

        if direct is not None:
            reply = self._finalize(direct)
            for piece in _chunk_text(reply):
                yield {"type": "token", "text": piece}
            yield self._done_event(reply, tool_calls)
            return

        film_direct = self._direct_film_grade_reply(tool_calls)
        if film_direct:
            reply = self._finalize(film_direct)
            for piece in _chunk_text(reply):
                yield {"type": "token", "text": piece}
            yield self._done_event(reply, tool_calls)
            return

        messages = answer_messages or self.memory.messages()
        yield from self._stream_answer(messages, stream_fn, tool_calls)

    def _stream_chat_imperative(
        self, user_text: str, *, stream_fn: Optional[StreamChatFn] = None
    ) -> Iterator[dict[str, Any]]:
        tool_calls: list[dict[str, Any]] = []
        observations: list[str] = []
        seen: set[str] = set()
        direct_reply: Optional[str] = None

        rounds = self._max_tool_rounds if self._skills is not None else 0
        for _ in range(rounds):
            raw = self._chat(self.memory.messages())
            call = _parse_tool_call(raw)
            if call is None:
                direct_reply = raw
                break
            merged = self._merge_turn_context_args(call["tool"], call["args"])
            key = f"{call['tool']}:{json.dumps(merged, sort_keys=True, ensure_ascii=False)}"
            if key in seen:
                break
            seen.add(key)
            tc = self._dispatch_call(call["tool"], merged)
            tool_calls.append(tc)
            observations.append(
                f"{call['tool']} -> {json.dumps({'ok': tc['ok'], 'metadata': tc['metadata']}, ensure_ascii=False)}"
            )
            yield {"type": "tool_call", **tc}

        if direct_reply is not None:
            reply = direct_reply if _parse_tool_call(direct_reply) is None else _NO_ANSWER_FALLBACK
            reply = self._finalize(reply)
            for piece in _chunk_text(reply):
                yield {"type": "token", "text": piece}
            yield self._done_event(reply, tool_calls)
            return

        if not tool_calls:
            yield from self._stream_answer(self.memory.messages(), stream_fn, tool_calls)
            return

        yield from self._stream_answer(
            self._build_final_answer_messages(user_text, observations), stream_fn, tool_calls
        )

    def _iter_final_tokens(
        self, messages: list[dict[str, str]], stream_fn: Optional[StreamChatFn]
    ) -> Iterator[str]:
        """Yield the final-answer tokens, real-streamed if possible, else chunked."""
        if stream_fn is not None:
            try:
                for piece in stream_fn(messages):
                    if piece:
                        yield piece
                return
            except Exception:  # transport hiccup mid-stream → fall back to one-shot
                logger.exception("stream_fn failed; falling back to non-streaming call")
        yield from _chunk_text(self._chat(messages))

    def _stream_answer(
        self,
        messages: list[dict[str, str]],
        stream_fn: Optional[StreamChatFn],
        tool_calls: list[dict[str, Any]],
    ) -> Iterator[dict[str, Any]]:
        """Stream final-answer tokens + a done event, buffering the head so a stray
        tool-call JSON is never shown (it is replaced with the fallback prose)."""
        head = ""
        committed = False
        toolish = False
        acc = ""
        for piece in self._iter_final_tokens(messages, stream_fn):
            acc += piece
            if committed:
                yield {"type": "token", "text": piece}
                continue
            head += piece
            stripped = head.lstrip()
            if not stripped:
                continue
            if stripped[0] == "{" or stripped.startswith("```"):
                toolish = True  # looks like a tool call; keep buffering silently
                continue
            committed = True
            yield {"type": "token", "text": head}

        if committed:
            reply = self._finalize(acc)
        else:
            if toolish or _parse_tool_call(acc) is not None:
                reply = _NO_ANSWER_FALLBACK
            else:
                reply = acc.strip() or _NO_ANSWER_FALLBACK
            reply = self._finalize(reply)
            yield {"type": "token", "text": reply}
        yield self._done_event(reply, tool_calls)

    def _build_turn_trace(
        self,
        reply: str,
        tool_calls: list[dict[str, Any]],
        *,
        routed: Optional[str] = None,
    ) -> dict[str, Any]:
        backend = str(self.last_backend or "")
        rule_id = routed
        if rule_id is None and backend.startswith("routed:"):
            rule_id = backend.split(":", 1)[1]
        grounding_hits = [e for e in self._events if e.get("type") == "grounding_violation"]
        guardrail_matches: list[str] = []
        for e in self._events:
            if e.get("type") == "guardrail" and e.get("triggered"):
                for m in e.get("matches") or []:
                    guardrail_matches.append(str(m))
        return {
            "schema_version": "agent_turn_trace.v1",
            "backend": backend or "unknown",
            "rule_id": rule_id,
            "rounds_used": len(tool_calls),
            "grounding_ok": not grounding_hits,
            "parse_fail": bool(self._turn_parse_fail),
            "parse_repaired": bool(self._turn_parse_repaired),
            "guardrail_matches": guardrail_matches,
            "json_leak": _parse_tool_call(reply or "") is not None,
        }

    def _attach_trace_to_done(
        self,
        reply: str,
        tool_calls: list[dict[str, Any]],
        *,
        routed: Optional[str] = None,
    ) -> dict[str, Any]:
        """Patch the latest ``done`` event (LangGraph) or emit an enriched one."""
        trace = self._build_turn_trace(reply, tool_calls, routed=routed)
        self.last_trace = trace
        for e in reversed(self._events):
            if e.get("type") == "done":
                e["reply"] = reply
                e["tool_calls"] = tool_calls
                e["routed"] = rule if (rule := trace.get("rule_id")) else e.get("routed")
                e["trace"] = trace
                e.update(trace)
                return e
        return self._done_event(reply, tool_calls, routed=routed, emit=True)

    def _done_event(
        self,
        reply: str,
        tool_calls: list[dict[str, Any]],
        *,
        routed: Optional[str] = None,
        emit: bool = True,
    ) -> dict[str, Any]:
        trace = self._build_turn_trace(reply, tool_calls, routed=routed)
        self.last_trace = trace
        ev = {
            "type": "done",
            "reply": reply,
            "tool_calls": tool_calls,
            "memory_turns": self.memory.turn_count,
            "working_memory": dict(self.working_memory),
            "routed": routed or trace.get("rule_id"),
            "trace": trace,
            **trace,
        }
        if emit:
            self._emit(ev)
        return ev

    def _lean_session_context(self) -> str:
        """Compact prefs + working memory for the lean final-answer prompt (no tool protocol)."""
        parts: list[str] = []
        prefs = _prefs_block_from_system(self.memory.system_prompt or "")
        if prefs:
            parts.append(prefs)
        wm = working_memory_prompt_block(self.working_memory)
        if wm:
            parts.append(wm)
        return "\n\n".join(parts)

    def _build_final_answer_messages(
        self, user_text: str, observations: list[str]
    ) -> list[dict[str, str]]:
        """Lean final-answer messages: tool results + prefs/WM, without the tool-call protocol."""
        joined = "\n".join(observations) if observations else "(no tool results)"
        system = _FINAL_ANSWER_SYSTEM
        ctx = self._lean_session_context()
        if ctx:
            system = (
                f"{system}\n\nSESSION CONTEXT (honor when wording; do not invent data "
                f"beyond tool results):\n{ctx}"
            )
        return [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"Question: {user_text}\n\nTool results:\n{joined}\n\n{_FINAL_ANSWER_NUDGE}",
            },
        ]

    def _direct_film_grade_reply(
        self, tool_calls: list[dict[str, Any]]
    ) -> Optional[str]:
        """Skip weak final-answer models for film grade tools (they invent styles from history)."""
        if not tool_calls:
            return None
        if any(tc.get("tool") not in _FILM_GRADE_TOOLS for tc in tool_calls):
            return None
        for tc in reversed(tool_calls):
            meta = tc.get("metadata") or {}
            reply = meta.get("reply_zh") or meta.get("error")
            if reply:
                return str(reply)
        return None

    def _force_final_answer(self, user_text: str, observations: list[str]) -> str:
        """Synthesize the final prose answer from a CLEAN, lean prompt.

        Drops the heavy tool-protocol system prompt and ``role:"tool"`` turns (weaker
        models ignore those and answer generically) but keeps a short SESSION CONTEXT
        with durable prefs + working memory so wording still honors user preferences.
        """
        direct = self._direct_film_grade_reply(self._turn_tool_calls())
        if direct:
            return direct
        messages = self._build_final_answer_messages(user_text, observations)
        final = self._chat(messages)
        if _parse_tool_call(final) is not None:
            self._turn_parse_fail = True
            return _NO_ANSWER_FALLBACK
        return final

    def _turn_tool_calls(self) -> list[dict[str, Any]]:
        return [e for e in self._events if e.get("type") == "tool_call"]

    def _finalize(self, reply: str) -> str:
        """Ground file cites, run output guardrails, and commit the reply to memory."""
        tool_calls = self._turn_tool_calls()
        grounded, verdict = ground_reply(
            reply,
            tool_calls=tool_calls,
            working_memory=self.working_memory,
        )
        if verdict.triggered:
            self._emit(
                {
                    "type": "grounding_violation",
                    "unknown": list(verdict.unknown),
                    "cited": list(verdict.cited),
                    "allowed": list(verdict.allowed),
                }
            )
            reply = grounded
        else:
            reply = grounded
        if self._guardrails is not None:
            reply = self._guardrails.mediate_output(reply)
        self.memory.add_assistant(reply)
        return reply
