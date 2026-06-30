"""Multi-turn conversational agent with memory, context-window management, and tools.

This is the dialogue counterpart to the single-shot curation loop: it keeps a running
conversation, trims it to a token budget (oldest turns first, optionally rolled into a
running summary), and on each user turn lets the model optionally call one Agent Skill
(see ``services/agent/skills``) before producing its final reply.

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
from typing import Any, Callable, Optional

from services.agent.guardrails import Guardrails
from services.agent.skills.base import SkillRegistry

logger = logging.getLogger(__name__)

# A chat backend: a list of {role, content} messages -> assistant text.
ChatFn = Callable[[list[dict[str, str]]], str]
# Optional summarizer for evicted turns: old messages -> a short summary string.
Summarizer = Callable[[list["Message"]], str]


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

    def add_tool_result(self, name: str, content: str) -> None:
        self._turns.append(Message("tool", content, name=name))
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


@dataclass
class TurnResult:
    reply: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


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
    """A stateful, multi-turn chat agent that can call skills mid-turn."""

    def __init__(
        self,
        chat_fn: ChatFn,
        *,
        memory: Optional[ConversationMemory] = None,
        skills: Optional[SkillRegistry] = None,
        guardrails: Optional[Guardrails] = None,
        max_tool_rounds: int = 3,
        wrap_tool_output: bool = True,
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

    def _record_tool_result(self, name: str, result) -> None:
        """Add a tool observation to memory, fencing it as untrusted only when asked."""
        obs = json.dumps(result.to_observation(), ensure_ascii=False)
        if self._guardrails is not None:
            if self._wrap_tool_output:
                obs = self._guardrails.guard_untrusted(obs, source=f"tool:{name}")
            else:
                # Still scan for injection (observability) without the heavy fence.
                self._guardrails.scan_input(obs, source=f"tool:{name}")
        self.memory.add_tool_result(name, obs)

    def chat(self, user_text: str) -> TurnResult:
        """Process one user turn: optional tool calls, then a final assistant reply."""
        if self._guardrails is not None:
            self._guardrails.scan_input(user_text, source="user")
        self.memory.add_user(user_text)
        tool_calls: list[dict[str, Any]] = []
        observations: list[str] = []
        seen: set[str] = set()

        rounds = self._max_tool_rounds if self._skills is not None else 0
        for _ in range(rounds):
            raw = self._chat(self.memory.messages())
            call = _parse_tool_call(raw)
            if call is None:
                return TurnResult(reply=self._finalize(raw), tool_calls=tool_calls)
            # Weak models often re-request a call they've already run instead of answering.
            # Detect the repeat and break to a forced final answer rather than looping.
            key = f"{call['tool']}:{json.dumps(call['args'], sort_keys=True, ensure_ascii=False)}"
            if key in seen:
                break
            seen.add(key)
            # Execute the requested skill and feed the observation back for the next round.
            result = self._skills.dispatch(call["tool"], call["args"])  # type: ignore[union-attr]
            self._record_tool_result(call["tool"], result)
            observations.append(f"{call['tool']} -> {json.dumps(result.to_observation(), ensure_ascii=False)}")
            tool_calls.append({"tool": call["tool"], "args": call["args"], "ok": result.ok})

        if not tool_calls:
            # No skills available → plain completion (no tool-result nudge).
            final = self._chat(self.memory.messages())
            if _parse_tool_call(final) is not None:
                final = _NO_ANSWER_FALLBACK
            return TurnResult(reply=self._finalize(final), tool_calls=tool_calls)

        # Tools done / budget spent / repeat detected → force a plain-language answer.
        final = self._force_final_answer(user_text, observations)
        return TurnResult(reply=self._finalize(final), tool_calls=tool_calls)

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
