"""Context-window governance for conversational / tool-using agents.

Keeps prompts bounded: truncate oversized tool observations, optionally compress
older turns into a rolling summary (wired via ``ConversationMemory.summarizer``).
"""
from __future__ import annotations

from typing import Any

# Soft caps — tuned for local instruct models with modest context windows.
DEFAULT_TOOL_RESULT_CHARS = 6000
DEFAULT_SINGLE_MESSAGE_CHARS = 8000


def truncate_text(text: str, max_chars: int, *, label: str = "content") -> str:
    """Truncate *text* to *max_chars*, appending a clear marker when cut."""
    s = text or ""
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    keep = max(0, max_chars - 48)
    return s[:keep] + f"\n…({label} truncated, {len(s)} chars)"


def truncate_tool_observation(
    observation: str,
    *,
    max_chars: int = DEFAULT_TOOL_RESULT_CHARS,
) -> str:
    return truncate_text(observation, max_chars, label="tool result")


def compress_working_memory(working: dict[str, Any], *, max_files: int = 30) -> dict[str, Any]:
    """Keep a compact, JSON-serializable working-memory snapshot for the next turn."""
    out: dict[str, Any] = {}
    files = working.get("last_files") or working.get("files") or []
    if isinstance(files, list):
        out["last_files"] = [str(f) for f in files[:max_files]]
    cites = working.get("last_citations") or working.get("citations") or []
    if isinstance(cites, list):
        slim = []
        for c in cites[:12]:
            if not isinstance(c, dict):
                continue
            slim.append(
                {
                    "file": c.get("file"),
                    "fused_score": c.get("fused_score"),
                    "caption": truncate_text(str(c.get("caption") or ""), 120, label="cap"),
                }
            )
        out["last_citations"] = slim
    for key in ("last_tool", "last_query", "last_rag_mode"):
        if working.get(key) is not None:
            out[key] = working[key]
    return out


def working_memory_prompt_block(working: dict[str, Any]) -> str:
    """Short system-prompt appendix for working memory (empty string if nothing useful)."""
    compact = compress_working_memory(working)
    if not compact:
        return ""
    files = compact.get("last_files") or []
    lines = ["WORKING MEMORY (from earlier tools this session):"]
    if compact.get("last_query"):
        lines.append(f"- last_query: {compact['last_query']}")
    if compact.get("last_tool"):
        lines.append(f"- last_tool: {compact['last_tool']}")
    if files:
        lines.append(f"- last_files ({len(files)}): {', '.join(files[:15])}")
    cites = compact.get("last_citations") or []
    for c in cites[:5]:
        lines.append(f"- cite {c.get('file')}: {c.get('caption')}")
    return "\n".join(lines)
