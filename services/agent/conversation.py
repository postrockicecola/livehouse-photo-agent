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
)
from services.agent.guardrails import Guardrails
from services.agent.skills.base import SkillRegistry

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
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
        return {"tool": obj["tool"], "args": obj.get("args") or {}}
    return None


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


# Clean, minimal context used to force a final answer. Weaker chat models (e.g. llava)
# revert to "how can I help?" when the heavy tool-protocol system prompt + role:"tool"
# messages are present, but answer correctly from a lean prompt that inlines the data.
_FINAL_ANSWER_SYSTEM = (
    "You are a concise assistant. Answer the user's question using ONLY the provided tool "
    "results. Do not output JSON and do not mention tools."
)
_FINAL_ANSWER_NUDGE = (
    "Using ONLY the tool results already shown above, answer my question now in plain, "
    "natural language. Do NOT output JSON and do NOT call any more tools."
)
_NO_ANSWER_FALLBACK = (
    "I gathered the data with the tools above but couldn't compose a final answer this "
    "turn. Please rephrase your question or ask about a specific photo or metric."
)


class ConversationalAgent:
    """A stateful, multi-turn chat agent that can call skills mid-turn.

    Default runtime is the LangGraph ``decide → act → answer`` subgraph
    (:mod:`services.agent.conversation_graph`); set ``LIVEHOUSE_AGENT_RUNTIME=imperative``
    to force the legacy while-loop.
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
        self._events: list[dict[str, Any]] = []
        self.last_backend: str = "imperative"

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
        if meta.get("files"):
            self.working_memory["last_files"] = list(meta.get("files") or [])
        if meta.get("citations"):
            self.working_memory["last_citations"] = list(meta.get("citations") or [])
        if meta.get("rag"):
            self.working_memory["last_rag_mode"] = (meta.get("rag") or {}).get("mode")
        self.working_memory = compress_working_memory(self.working_memory)

    def _record_tool_result(self, name: str, result) -> None:
        """Add a tool observation to memory, fencing it as untrusted only when asked."""
        obs = json.dumps(result.to_observation(), ensure_ascii=False)
        if self._guardrails is not None:
            if self._wrap_tool_output:
                obs = self._guardrails.guard_untrusted(obs, source=f"tool:{name}")
            else:
                # Still scan for injection (observability) without the heavy fence.
                self._guardrails.scan_input(obs, source=f"tool:{name}")
        self.memory.add_tool_result(name, obs, max_chars=self._max_tool_result_chars)

    def _graph_kwargs(self) -> dict[str, Any]:
        return {
            "chat_fn": self._chat,
            "memory": self.memory,
            "skills": self._skills,
            "guardrails": self._guardrails,
            "wrap_tool_output": self._wrap_tool_output,
            "max_tool_result_chars": self._max_tool_result_chars,
            "update_working_memory": self._update_working_memory,
            "record_tool_result": self._record_tool_result,
            "finalize": self._finalize,
            "force_final_answer": self._force_final_answer,
            "parse_tool_call": _parse_tool_call,
            "emit": self._emit,
            "no_answer_fallback": _NO_ANSWER_FALLBACK,
            "final_answer_system": _FINAL_ANSWER_SYSTEM,
            "final_answer_nudge": _FINAL_ANSWER_NUDGE,
        }

    def _langgraph_enabled(self) -> bool:
        from services.agent.conversation_graph import chat_runtime_preference, langgraph_available

        return chat_runtime_preference() == "langgraph" and langgraph_available()

    def _try_langgraph_turn(self, user_text: str, *, defer_answer: bool = False) -> Optional[dict[str, Any]]:
        from services.agent.conversation_graph import run_chat_turn

        if not self._langgraph_enabled():
            return None
        try:
            return dict(
                run_chat_turn(
                    user_text=user_text,
                    max_tool_rounds=self._max_tool_rounds,
                    defer_answer=defer_answer,
                    **self._graph_kwargs(),
                )
            )
        except Exception:
            logger.exception("LangGraph chat turn failed; falling back to imperative loop")
            return None

    def chat(self, user_text: str) -> TurnResult:
        """Process one user turn: optional tool calls, then a final assistant reply."""
        if self._guardrails is not None:
            self._guardrails.scan_input(user_text, source="user")
        self.memory.add_user(user_text)

        state = self._try_langgraph_turn(user_text, defer_answer=False)
        if state is not None:
            self.last_backend = str(state.get("backend") or "langgraph")
            return TurnResult(
                reply=str(state.get("reply") or ""),
                tool_calls=list(state.get("tool_calls") or []),
                working_memory=dict(self.working_memory),
                events=list(self._events),
            )

        self.last_backend = "imperative"
        return self._chat_imperative(user_text)

    def _chat_imperative(self, user_text: str) -> TurnResult:
        tool_calls: list[dict[str, Any]] = []
        observations: list[str] = []
        seen: set[str] = set()

        rounds = self._max_tool_rounds if self._skills is not None else 0
        for _ in range(rounds):
            raw = self._chat(self.memory.messages())
            call = _parse_tool_call(raw)
            if call is None:
                reply = self._finalize(raw)
                self._emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
                return TurnResult(
                    reply=reply,
                    tool_calls=tool_calls,
                    working_memory=dict(self.working_memory),
                    events=list(self._events),
                )
            key = f"{call['tool']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
            if key in seen:
                break
            seen.add(key)
            result = self._skills.dispatch(call["tool"], call["args"])  # type: ignore[union-attr]
            self._update_working_memory(call["tool"], call["args"], result)
            self._record_tool_result(call["tool"], result)
            observations.append(f"{call['tool']} -> {json.dumps(result.to_observation(), ensure_ascii=False)}")
            tc = {
                "tool": call["tool"],
                "args": call["args"],
                "ok": result.ok,
                "metadata": getattr(result, "metadata", None) or {},
            }
            tool_calls.append(tc)
            self._emit({"type": "tool_call", **tc})

        if not tool_calls:
            final = self._chat(self.memory.messages())
            if _parse_tool_call(final) is not None:
                final = _NO_ANSWER_FALLBACK
            reply = self._finalize(final)
            self._emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
            return TurnResult(
                reply=reply,
                tool_calls=tool_calls,
                working_memory=dict(self.working_memory),
                events=list(self._events),
            )

        final = self._force_final_answer(user_text, observations)
        reply = self._finalize(final)
        self._emit({"type": "done", "reply": reply, "tool_calls": tool_calls})
        return TurnResult(
            reply=reply,
            tool_calls=tool_calls,
            working_memory=dict(self.working_memory),
            events=list(self._events),
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
        if self._guardrails is not None:
            self._guardrails.scan_input(user_text, source="user")
        self.memory.add_user(user_text)

        if self._langgraph_enabled():
            try:
                yield from self._stream_chat_langgraph(user_text, stream_fn=stream_fn)
                return
            except Exception:
                logger.exception("LangGraph streaming chat failed; falling back to imperative loop")

        self.last_backend = "imperative"
        yield from self._stream_chat_imperative(user_text, stream_fn=stream_fn)

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
            key = f"{call['tool']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
            if key in seen:
                break
            seen.add(key)
            result = self._skills.dispatch(call["tool"], call["args"])  # type: ignore[union-attr]
            self._update_working_memory(call["tool"], call["args"], result)
            self._record_tool_result(call["tool"], result)
            observations.append(
                f"{call['tool']} -> {json.dumps(result.to_observation(), ensure_ascii=False)}"
            )
            tc = {
                "tool": call["tool"],
                "args": call["args"],
                "ok": result.ok,
                "metadata": getattr(result, "metadata", None) or {},
            }
            tool_calls.append(tc)
            self._emit({"type": "tool_call", **tc})
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

        joined = "\n".join(observations) if observations else "(no tool results)"
        messages = [
            {"role": "system", "content": _FINAL_ANSWER_SYSTEM},
            {"role": "user", "content": f"Question: {user_text}\n\nTool results:\n{joined}\n\n{_FINAL_ANSWER_NUDGE}"},
        ]
        yield from self._stream_answer(messages, stream_fn, tool_calls)

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

    def _done_event(self, reply: str, tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
        ev = {
            "type": "done",
            "reply": reply,
            "tool_calls": tool_calls,
            "memory_turns": self.memory.turn_count,
            "working_memory": dict(self.working_memory),
        }
        self._emit(ev)
        return ev

    def _force_final_answer(self, user_text: str, observations: list[str]) -> str:
        """Synthesize the final prose answer from a CLEAN, lean prompt.

        We deliberately drop the tool-protocol system prompt and ``role:"tool"`` messages
        here: weaker models ignore them and answer generically, but reliably answer when
        the question + tool results are inlined in a minimal prompt. Strong models normally
        answer in-loop and never reach this path.
        """
        joined = "\n".join(observations) if observations else "(no tool results)"
        messages = [
            {"role": "system", "content": _FINAL_ANSWER_SYSTEM},
            {"role": "user", "content": f"Question: {user_text}\n\nTool results:\n{joined}\n\n{_FINAL_ANSWER_NUDGE}"},
        ]
        final = self._chat(messages)
        if _parse_tool_call(final) is not None:
            return _NO_ANSWER_FALLBACK
        return final

    def _finalize(self, reply: str) -> str:
        """Run output guardrails (for observability) and commit the reply to memory."""
        if self._guardrails is not None:
            self._guardrails.check_output(reply)
        self.memory.add_assistant(reply)
        return reply
