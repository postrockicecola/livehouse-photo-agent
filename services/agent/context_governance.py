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


# Match gallery_search max limit so a full shortlist (e.g. 40 keepers) survives turns.
DEFAULT_WORKING_MEMORY_FILES = 100
DEFAULT_SELECTION_HISTORY_RECORDS = 20
DEFAULT_SELECTION_HISTORY_FILES = DEFAULT_WORKING_MEMORY_FILES
DEFAULT_SELECTION_HISTORY_PROMPT_FILES = 30


def _compact_selection_history(
    working: dict[str, Any],
    *,
    max_records: int = DEFAULT_SELECTION_HISTORY_RECORDS,
    max_files: int = DEFAULT_SELECTION_HISTORY_FILES,
) -> tuple[list[dict[str, Any]], str]:
    raw_history = working.get("selection_history")
    history: list[dict[str, Any]] = []
    if isinstance(raw_history, list):
        for i, raw in enumerate(raw_history):
            if not isinstance(raw, dict):
                continue
            files = raw.get("files")
            if not isinstance(files, list):
                continue
            clean_files = [str(f) for f in files[:max_files] if str(f).strip()]
            if not clean_files:
                continue
            try:
                turn_id = max(1, int(raw.get("turn_id") or i + 1))
            except (TypeError, ValueError):
                turn_id = i + 1
            selection_id = str(raw.get("selection_id") or f"sel_{turn_id:03d}")
            history.append(
                {
                    "turn_id": turn_id,
                    "selection_id": selection_id,
                    "query": truncate_text(str(raw.get("query") or ""), 160, label="query"),
                    "files": clean_files,
                    "created_at": str(raw.get("created_at") or ""),
                }
            )

    legacy_files = working.get("last_files") or working.get("files") or working.get("selected_keys")
    if not history and isinstance(legacy_files, list):
        clean_legacy = [str(f) for f in legacy_files[:max_files] if str(f).strip()]
        if clean_legacy:
            history.append(
                {
                    "turn_id": 1,
                    "selection_id": "legacy",
                    "query": str(working.get("last_query") or "历史记录"),
                    "files": clean_legacy,
                    "created_at": "",
                }
            )

    history = history[-max(1, int(max_records)) :]
    valid_ids = {str(row["selection_id"]) for row in history}
    active = str(working.get("active_selection_id") or "")
    if active not in valid_ids:
        active = str(history[-1]["selection_id"]) if history else ""
    return history, active


def compress_working_memory(
    working: dict[str, Any], *, max_files: int = DEFAULT_WORKING_MEMORY_FILES
) -> dict[str, Any]:
    """Keep a compact, JSON-serializable working-memory snapshot for the next turn."""
    out: dict[str, Any] = {}
    history, active_selection_id = _compact_selection_history(working)
    active_record = next(
        (
            row
            for row in history
            if str(row.get("selection_id") or "") == active_selection_id
        ),
        None,
    )
    files = (
        (active_record or {}).get("files")
        or working.get("last_files")
        or working.get("files")
        or working.get("selected_keys")
        or []
    )
    if isinstance(files, list):
        out["last_files"] = [str(f) for f in files[:max_files] if str(f).strip()]
    archive_hits = working.get("last_archive_hits") or []
    if isinstance(archive_hits, list) and archive_hits:
        out["last_archive_hits"] = [
            str(f) for f in archive_hits[:DEFAULT_SELECTION_HISTORY_PROMPT_FILES] if str(f).strip()
        ]
    if history:
        out["selection_history"] = history
        out["active_selection_id"] = active_selection_id
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
    for key in ("last_tool", "last_query", "last_archive_query"):
        if working.get(key) is not None:
            out[key] = working[key]
    return out


def format_selection_history_for_prompt(working: dict[str, Any]) -> str:
    """Render a compact, implicit index for resolving historical selection references."""
    compact = compress_working_memory(working)
    history = compact.get("selection_history") or []
    if not isinstance(history, list) or not history:
        return ""
    lines = ["[Selection History / 历史选片记录]"]
    active = str(compact.get("active_selection_id") or "")
    active_turn: int | None = None
    for row in history:
        if not isinstance(row, dict):
            continue
        turn_id = int(row.get("turn_id") or 0)
        selection_id = str(row.get("selection_id") or "")
        query = str(row.get("query") or "未命名选片").replace("\n", " ")
        row_files = list(row.get("files") or [])
        files = ", ".join(
            str(f) for f in row_files[:DEFAULT_SELECTION_HISTORY_PROMPT_FILES]
        )
        if len(row_files) > DEFAULT_SELECTION_HISTORY_PROMPT_FILES:
            files = f"{files}, …(+{len(row_files) - DEFAULT_SELECTION_HISTORY_PROMPT_FILES})"
        lines.append(f'- Turn {turn_id} ({selection_id}): "{query}" -> [{files}]')
        if selection_id == active:
            active_turn = turn_id
    if active:
        label = f"Turn {active_turn} " if active_turn is not None else ""
        lines.append(f"Current Active Selection: {label}({active})")
    lines.append(
        "Resolve 第X轮/上一轮/刚才选的… against this index; reuse its files without "
        "asking the user to list filenames."
    )
    return "\n".join(lines)


def working_memory_prompt_block(working: dict[str, Any]) -> str:
    """Short system-prompt appendix for working memory (empty string if nothing useful)."""
    compact = compress_working_memory(working)
    if not compact:
        return ""
    files = compact.get("last_files") or []
    lines = [
        "WORKING MEMORY (from earlier tools this session — reuse these files when the user "
        "says 刚才选出的 / 这些 / 那批; NEVER ask the user to re-list filenames):"
    ]
    if compact.get("last_query"):
        lines.append(f"- last_query: {compact['last_query']}")
    if compact.get("last_tool"):
        lines.append(f"- last_tool: {compact['last_tool']}")
    if files:
        lines.append(f"- last_files ({len(files)}): {', '.join(files)}")
    archive_hits = compact.get("last_archive_hits") or []
    if compact.get("last_archive_query"):
        lines.append(f"- last_archive_query: {compact['last_archive_query']}")
    if archive_hits:
        lines.append(
            f"- last_archive_hits ({len(archive_hits)}; not current-session selections): "
            + ", ".join(str(f) for f in archive_hits)
        )
    cites = compact.get("last_citations") or []
    for c in cites[:5]:
        lines.append(f"- cite {c.get('file')}: {c.get('caption')}")
    history_block = format_selection_history_for_prompt(compact)
    if history_block:
        lines.extend(["", history_block])
    return "\n".join(lines)
