"""Structured final answer derived from tool results + prose summary.

UI / eval can consume :class:`FinalAnswer` while the user still sees ``summary`` prose.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional


@dataclass
class FinalAnswer:
    schema_version: str = "agent_final_answer.v1"
    summary: str = ""
    files: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _files_from_tool_calls(tool_calls: Iterable[dict[str, Any]] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        for key in ("files", "selected_keys"):
            for f in meta.get(key) or []:
                name = str(f or "").strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
    return out


def _actions_from_tool_calls(tool_calls: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for tc in tool_calls or []:
        if not isinstance(tc, dict) or not tc.get("ok"):
            continue
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        ui = meta.get("ui_action")
        if not ui:
            continue
        actions.append(
            {
                "ui_action": ui,
                "tool": tc.get("tool"),
                "session_vibe": meta.get("session_vibe"),
                "files": list(meta.get("files") or meta.get("selected_keys") or [])[:40],
            }
        )
    return actions


def build_final_answer(
    summary: str,
    *,
    tool_calls: Iterable[dict[str, Any]] | None = None,
    working_memory: Optional[dict[str, Any]] = None,
) -> FinalAnswer:
    """Post-hoc structure: prose summary + files/actions from this turn's tools."""
    files = _files_from_tool_calls(tool_calls)
    if not files and working_memory:
        for f in working_memory.get("last_files") or []:
            name = str(f or "").strip()
            if name and name not in files:
                files.append(name)
    return FinalAnswer(
        summary=str(summary or "").strip(),
        files=files[:40],
        actions=_actions_from_tool_calls(tool_calls),
        ok=True,
    )


def prose_from_final(answer: FinalAnswer) -> str:
    """User-visible text — currently the summary (UI may also render files/actions)."""
    return str(answer.summary or "").strip()
