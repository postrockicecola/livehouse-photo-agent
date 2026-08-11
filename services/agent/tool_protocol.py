"""Unified tool-call surface for Gallery chat decide→act.

Covers:
- text-protocol parse (``{"tool","args"}``)
- multi native ``tool_calls`` → serial ``__multi__`` batch
- broken-JSON intent sniff + one-shot repair
- native OpenAI/Ollama ``tool_calls`` → text-protocol bridge

Production decide nodes should go through :func:`resolve_tool_decision` so
parse / repair / native bridge stay one exit.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

ChatFn = Callable[[list[dict[str, str]]], str]

REPAIR_NUDGE = (
    "Your previous reply was not valid tool JSON. "
    'Reply with ONLY a single JSON object {"tool":"<name>","args":{...}} '
    "or a plain natural-language answer with no JSON."
)

# Synthetic tool: args.calls = [{tool, args}, ...] — executed serially (or in
# parallel when all are read-only; see conversation_graph.act).
MULTI_TOOL = "__multi__"

# Read-only skills safe to run concurrently inside a multi batch.
READ_ONLY_TOOLS = frozenset(
    {
        "archive_search",
        "gallery_search",
        "gallery_stats",
        "knowledge_search",
        "explain_photo",
        "list_preferences",
        "retrieve_selection_experience",
    }
)


@dataclass(frozen=True)
class ToolDecision:
    """Resolved model action for one decide step."""

    call: Optional[dict[str, Any]]
    raw: str
    repaired: bool = False
    source: str = "text"  # text | repair | native


def parse_tool_call(text: str) -> Optional[dict[str, Any]]:
    """Extract a ``{"tool": name, "args": {...}}`` object from model output, if present."""
    s = (text or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1 :]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
        return {"tool": obj["tool"], "args": obj.get("args") or {}}
    return None


def looks_like_tool_intent(text: str) -> bool:
    """True when the model probably tried to emit a tool call but parse failed/partial."""
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("```"):
        return True
    if '"tool"' in s or "'tool'" in s:
        return True
    # Broken JSON that still opens an object — common weak-model failure.
    if s.startswith("{") and ("tool" in s.lower() or "args" in s.lower()):
        return True
    return False


def parse_tool_call_with_repair(
    chat_fn: ChatFn,
    messages: list[dict[str, str]],
    raw: str,
) -> tuple[Optional[dict[str, Any]], str, bool]:
    """Parse ``raw``; if it looks like a broken tool call, run one repair completion.

    Returns ``(call, effective_raw, repaired)``.
    ``repaired`` is True when a repair round was attempted (success or fail).
    """
    call = parse_tool_call(raw)
    if call is not None:
        return call, raw, False
    if not looks_like_tool_intent(raw):
        return None, raw, False

    repair_messages = list(messages) + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": REPAIR_NUDGE},
    ]
    try:
        repaired_raw = chat_fn(repair_messages)
    except Exception:
        return None, raw, True
    call2 = parse_tool_call(repaired_raw)
    if call2 is not None:
        return call2, repaired_raw, True
    return None, raw, True


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_native_tool_calls(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Normalize Ollama/OpenAI ``message.tool_calls`` to ``[{tool, args}, ...]``."""
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        return []
    out: list[dict[str, Any]] = []
    for tc in raw_calls:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        args = _parse_arguments(fn.get("arguments") if "arguments" in fn else fn.get("args"))
        out.append({"tool": name, "args": args})
    return out


def pack_tool_calls(calls: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pack one or many calls into the text-protocol shape (multi → ``__multi__``)."""
    clean: list[dict[str, Any]] = []
    for c in calls or []:
        if not isinstance(c, dict):
            continue
        name = str(c.get("tool") or "").strip()
        if not name or name == MULTI_TOOL:
            continue
        clean.append({"tool": name, "args": dict(c.get("args") or {})})
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    return {"tool": MULTI_TOOL, "args": {"calls": clean}}


def expand_tool_calls(call: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Expand a pending call into a serial list (``__multi__`` → children)."""
    if not call:
        return []
    tool = str(call.get("tool") or "")
    args = dict(call.get("args") or {})
    if tool != MULTI_TOOL:
        return [{"tool": tool, "args": args}]
    out: list[dict[str, Any]] = []
    for sub in args.get("calls") or []:
        if not isinstance(sub, dict):
            continue
        name = str(sub.get("tool") or "").strip()
        if not name or name == MULTI_TOOL:
            continue
        out.append({"tool": name, "args": dict(sub.get("args") or {})})
    return out


def all_read_only(calls: list[dict[str, Any]]) -> bool:
    if not calls:
        return False
    return all(str(c.get("tool") or "") in READ_ONLY_TOOLS for c in calls)


def content_from_assistant_message(message: Mapping[str, Any]) -> str:
    """Bridge native ``tool_calls`` into the text-protocol JSON the agent already parses.

    One call → ``{"tool","args"}``; several → ``{"tool":"__multi__","args":{"calls":[...]}}``.
    """
    calls = normalize_native_tool_calls(message)
    packed = pack_tool_calls(calls)
    if packed is not None:
        return json.dumps(packed, ensure_ascii=False)
    content = message.get("content")
    if isinstance(content, list):  # some servers return content parts
        return "".join(str(p.get("text", "")) for p in content if isinstance(p, dict)).strip()
    return str(content or "").strip()


def resolve_tool_decision(
    chat_fn: ChatFn,
    messages: list[dict[str, str]],
    raw: str,
) -> ToolDecision:
    """Single exit used by decide: parse → optional repair → ToolDecision."""
    call, effective, repaired = parse_tool_call_with_repair(chat_fn, messages, raw)
    source = "repair" if repaired else "text"
    if call is not None and call.get("tool") == MULTI_TOOL:
        source = "native" if not repaired else "repair"
    return ToolDecision(call=call, raw=effective, repaired=repaired, source=source)


def native_tools_preference(*, provider: str | None = None) -> str:
    """Return ``on`` | ``off`` | ``auto`` from ``LIVEHOUSE_AGENT_NATIVE_TOOLS``."""
    raw = (os.environ.get("LIVEHOUSE_AGENT_NATIVE_TOOLS") or "auto").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return "on"
    if raw in ("0", "false", "no", "off"):
        return "off"
    return "auto"


def native_tools_enabled(
    explicit: Optional[bool] = None,
    *,
    provider: str | None = None,
) -> bool:
    """Whether to attach OpenAI-shaped ``tools`` on chat requests.

    - ``explicit`` wins when not ``None``
    - env ``1/true`` → on; ``0/false`` → off
    - default / ``auto``: on for ``openai`` / ``vllm``, off for ``ollama`` (weak local)
    """
    if explicit is not None:
        return bool(explicit)
    pref = native_tools_preference()
    if pref == "on":
        return True
    if pref == "off":
        return False
    prov = (provider or "").strip().lower()
    return prov in ("openai", "vllm")
