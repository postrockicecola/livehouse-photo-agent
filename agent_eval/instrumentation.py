"""Non-invasive instrumentation around the real chat backend and skill registry."""
from __future__ import annotations

import json
import time
from typing import Any, Callable

from services.agent.conversation import _parse_tool_call


def approx_tokens(value: Any) -> int:
    """Portable fallback when the production ChatFn does not expose provider usage."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return max(1, (len(value) + 3) // 4) if value else 0


def _clip(value: Any, limit: int = 20_000) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit], "original_chars": len(text)}


class InstrumentedChat:
    """Record model spans while retaining the exact production ChatFn behavior."""

    def __init__(self, chat_fn: Callable[[list[dict[str, Any]]], str]) -> None:
        self._chat_fn = chat_fn
        self.calls: list[dict[str, Any]] = []

    def __call__(self, messages: list[dict[str, Any]]) -> str:
        started = time.time()
        t0 = time.monotonic()
        prompt_tokens = approx_tokens(messages)
        call: dict[str, Any] = {
            "index": len(self.calls) + 1,
            "started_at": started,
            "message_count": len(messages),
            "prompt_tokens": prompt_tokens,
            "token_source": "estimated",
        }
        try:
            output = self._chat_fn(messages)
            call["output"] = _clip(output)
            call["completion_tokens"] = approx_tokens(output)
            decision = _parse_tool_call(output)
            call["planner_output"] = decision
            call["ok"] = True
            return output
        except Exception as exc:
            call.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            raise
        finally:
            call["latency_ms"] = round((time.monotonic() - t0) * 1000, 3)
            call["total_tokens"] = int(call.get("prompt_tokens", 0)) + int(
                call.get("completion_tokens", 0)
            )
            self.calls.append(call)


class InstrumentedRegistry:
    """Proxy a SkillRegistry and capture parameters, outputs, failures, and latency."""

    def __init__(self, registry: Any) -> None:
        self._registry = registry
        self.calls: list[dict[str, Any]] = []

    def tool_specs(self) -> list[dict[str, Any]]:
        return self._registry.tool_specs()

    def dispatch(self, name: str, args: dict[str, Any] | None = None) -> Any:
        started = time.time()
        t0 = time.monotonic()
        call: dict[str, Any] = {
            "index": len(self.calls) + 1,
            "tool": name,
            "parameters": dict(args or {}),
            "started_at": started,
        }
        try:
            result = self._registry.dispatch(name, args)
            call.update(
                {
                    "ok": bool(result.ok),
                    "output": _clip(result.to_observation()),
                    "error": result.error,
                }
            )
            return result
        except Exception as exc:
            call.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            raise
        finally:
            call["latency_ms"] = round((time.monotonic() - t0) * 1000, 3)
            self.calls.append(call)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)


def memory_delta(
    before_working: dict[str, Any],
    after_working: dict[str, Any],
    before_prefs: dict[str, str],
    after_prefs: dict[str, str],
) -> dict[str, Any]:
    def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        return {
            key: {"before": before.get(key), "after": after.get(key)}
            for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)
        }

    return {
        "working_memory": _delta(before_working, after_working),
        "preferences": _delta(before_prefs, after_prefs),
    }

