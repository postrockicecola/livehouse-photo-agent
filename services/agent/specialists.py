"""Thin specialist allowlists for Gallery chat (retrieve / select / style).

Not a second orchestrator: the compile-once graph stays the same. ``plan``
assigns a specialist; ``act`` rejects tools outside that specialist's set.
``general`` keeps the full registry (fallback).
"""
from __future__ import annotations

import re
from typing import Any, Literal, Optional

SpecialistId = Literal["retrieve", "select", "style", "general"]

SPECIALIST_CHECKLIST: tuple[dict[str, str], ...] = (
    {
        "id": "retrieve",
        "when": "search / find / archive / stats without a select or delivery ask",
        "path": "read-only search skills only",
    },
    {
        "id": "select",
        "when": "shortlist / 初选 / 这场交片 / async curation job",
        "path": "search + select + curation job skills",
    },
    {
        "id": "style",
        "when": "film / vibe / export without a competing select ask",
        "path": "film + export skills",
    },
    {
        "id": "general",
        "when": "no specialist cue (help, memory, mixed, or unknown)",
        "path": "full decide→act registry",
    },
)

RETRIEVE_TOOLS = frozenset(
    {
        "archive_search",
        "gallery_search",
        "gallery_stats",
        "explain_photo",
        "knowledge_search",
        "retrieve_selection_experience",
        "list_preferences",
        "write_artifact",
    }
)
SELECT_TOOLS = frozenset(
    {
        "gallery_search",
        "gallery_select",
        "submit_curation_job",
        "poll_curation_job",
        "cancel_curation_job",
        "mark_score_gap",
        "retrieve_selection_experience",
        "write_artifact",
    }
)
STYLE_TOOLS = frozenset(
    {
        "recommend_film_for_photo",
        "apply_film_vibe",
        "export_selected",
        "explain_photo",
    }
)

SPECIALIST_TOOLS: dict[SpecialistId, Optional[frozenset[str]]] = {
    "retrieve": RETRIEVE_TOOLS,
    "select": SELECT_TOOLS,
    "style": STYLE_TOOLS,
    "general": None,
}

_RETRIEVE_RE = re.compile(
    r"(找|搜索|搜一下|有没有|archive|跨场|解释|为什么|多少张|统计|gallery_stats)",
    re.IGNORECASE,
)
_SELECT_RE = re.compile(
    r"(选出|挑选|初选|交片|短名单|收藏|标出|这场交|异步交片|后台交片)",
    re.IGNORECASE,
)
_STYLE_RE = re.compile(
    r"(胶片|风格|导出|export|cinestill|portra|修成|vibe)",
    re.IGNORECASE,
)


def specialist_tool_names(specialist: str) -> Optional[frozenset[str]]:
    """Allowed tool names for ``specialist``, or ``None`` when unrestricted."""
    return SPECIALIST_TOOLS.get(str(specialist or "general") or "general")


def specialist_allows(specialist: str, tool: str) -> bool:
    allowed = specialist_tool_names(specialist)
    if allowed is None:
        return True
    return str(tool or "") in allowed


def filter_tool_specs(specs: list[dict[str, Any]], specialist: str) -> list[dict[str, Any]]:
    """Keep OpenAI-shaped ``tools`` entries that the specialist may call."""
    allowed = specialist_tool_names(specialist)
    if allowed is None:
        return list(specs or [])
    out: list[dict[str, Any]] = []
    for spec in specs or []:
        if not isinstance(spec, dict):
            continue
        fn = spec.get("function") if isinstance(spec.get("function"), dict) else spec
        name = str((fn or {}).get("name") or "").strip()
        if name and name in allowed:
            out.append(spec)
    return out


def assign_specialist(
    user_text: str,
    *,
    planned_route: Optional[dict[str, Any]] = None,
) -> SpecialistId:
    """Pick retrieve|select|style|general from a planned route or user text."""
    route = planned_route if isinstance(planned_route, dict) else {}
    rule = str(route.get("rule_id") or "")
    if rule == "async_curation_job" or bool(route.get("select_after_search")):
        return "select"
    if rule in ("apply_film_vibe", "recommend_film_for_photo"):
        return "style"
    tools = [
        str(call.get("tool") or "")
        for call in (route.get("calls") or [])
        if isinstance(call, dict)
    ]
    if any(tool in STYLE_TOOLS and tool not in RETRIEVE_TOOLS for tool in tools):
        return "style"
    if any(tool in SELECT_TOOLS and tool not in RETRIEVE_TOOLS for tool in tools):
        return "select"
    if any(tool in RETRIEVE_TOOLS for tool in tools):
        return "retrieve"

    text = (user_text or "").strip()
    if not text:
        return "general"
    if _STYLE_RE.search(text) and _SELECT_RE.search(text) is None:
        return "style"
    if _SELECT_RE.search(text):
        return "select"
    if _RETRIEVE_RE.search(text):
        return "retrieve"
    return "general"
