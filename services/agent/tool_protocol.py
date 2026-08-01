"""Text-protocol tool JSON helpers: parse, intent sniff, one-shot repair."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

ChatFn = Callable[[list[dict[str, str]]], str]

REPAIR_NUDGE = (
    "Your previous reply was not valid tool JSON. "
    'Reply with ONLY a single JSON object {"tool":"<name>","args":{...}} '
    "or a plain natural-language answer with no JSON."
)


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
