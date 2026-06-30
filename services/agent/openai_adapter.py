"""Adapt the project's :class:`SkillRegistry` to the OpenAI function-calling protocol.

The repo deliberately hand-rolls its agent loop (so the internals are explainable), but
the industry-standard surface is the OpenAI / vLLM *function-calling* wire format, which
LangGraph, the OpenAI Agents SDK, AutoGen and CrewAI all speak underneath. This module
is the bridge: it renders the registry as OpenAI ``tools`` and runs the canonical
tool-calling cycle, so the same skills plug into any of those stacks unchanged.

Protocol implemented (OpenAI Chat Completions tool calling):

1. Send ``messages`` + ``tools`` to the model.
2. If the assistant message contains ``tool_calls`` (each with an ``id`` and a
   ``function.name`` + JSON ``function.arguments``), execute each against the registry
   and append one ``{"role": "tool", "tool_call_id": ..., "content": ...}`` message.
3. Loop until the model answers with plain ``content`` or the round budget is spent.

Framework mapping (the "I know the equivalent" cheat-sheet):

- ``SkillRegistry.tool_specs()`` ≡ LangGraph ``ToolNode`` tools / OpenAI Agents SDK
  ``tools=[...]`` / AutoGen ``register_for_llm``.
- ``run_openai_tool_loop`` ≡ LangGraph's ``agent → tools → agent`` cycle (this function
  *is* that graph, just written out) / the Agents SDK ``Runner`` loop.
- ``role: "tool"`` messages ≡ LangGraph ``ToolMessage`` / Agents SDK tool outputs.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from services.agent.skills.base import SkillRegistry

logger = logging.getLogger(__name__)

# A chat-completions backend: (messages, tools) -> an OpenAI-shaped response.
# May return either a full response ({"choices": [{"message": {...}}]}) or just the
# assistant message dict; both are handled.
ChatCompletionFn = Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]]


def tools_for_openai(registry: SkillRegistry) -> list[dict[str, Any]]:
    """The registry as an OpenAI/vLLM ``tools`` array (alias of ``tool_specs``)."""
    return registry.tool_specs()


def _extract_message(response: dict[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if isinstance(choices, list) and choices:
        return dict(choices[0].get("message") or {})
    return dict(response)  # already a message-shaped dict


def _parse_arguments(raw: Any) -> dict[str, Any]:
    """OpenAI sends ``function.arguments`` as a JSON *string*; tolerate dicts too."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


@dataclass
class OpenAIToolLoopResult:
    content: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    rounds: int = 0


def run_openai_tool_loop(
    chat_completion_fn: ChatCompletionFn,
    registry: SkillRegistry,
    messages: list[dict[str, Any]],
    *,
    max_rounds: int = 4,
) -> OpenAIToolLoopResult:
    """Run the standard OpenAI function-calling cycle against the skill registry."""
    convo: list[dict[str, Any]] = list(messages)
    tools = tools_for_openai(registry)
    executed: list[dict[str, Any]] = []

    for rnd in range(1, max_rounds + 1):
        response = chat_completion_fn(convo, tools)
        message = _extract_message(response)
        tool_calls = message.get("tool_calls") or []

        if not tool_calls:
            content = str(message.get("content") or "")
            convo.append({"role": "assistant", "content": content})
            return OpenAIToolLoopResult(content=content, messages=convo, tool_calls=executed, rounds=rnd)

        # Echo the assistant tool-call message, then answer each call with a tool message.
        convo.append({"role": "assistant", "content": message.get("content"), "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = str(fn.get("name") or "")
            args = _parse_arguments(fn.get("arguments"))
            result = registry.dispatch(name, args)
            convo.append({
                "role": "tool",
                "tool_call_id": tc.get("id"),
                "name": name,
                "content": json.dumps(result.to_observation(), ensure_ascii=False),
            })
            executed.append({"id": tc.get("id"), "name": name, "args": args, "ok": result.ok})

    # Round budget exhausted while the model still wanted tools.
    fallback = "Reached the tool-call round limit before a final answer."
    convo.append({"role": "assistant", "content": fallback})
    return OpenAIToolLoopResult(content=fallback, messages=convo, tool_calls=executed, rounds=max_rounds)
