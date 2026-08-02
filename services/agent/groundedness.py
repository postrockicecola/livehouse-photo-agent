"""Final-answer groundedness: replies may only cite files returned by tools / WM.

Cheap basename heuristic — not a full entity linker. Goal: block the common
failure mode where a weak chat model invents ``DSC_9999.jpg`` after a search.

v1 rewrite prefers *surgical strip* of unknown basenames so honest prose /
allowed cites survive; falls back to a template only when little remains.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Concert / gallery outputs we actually serve; keep the list tight to limit FPs.
_FILE_RE = re.compile(
    r"(?i)\b([A-Za-z0-9][A-Za-z0-9._-]{0,180}\."
    r"(?:jpe?g|png|webp|gif|arw|cr2|nef|dng|heic|tiff?))\b"
)
_WS_RE = re.compile(r"[ \t]{2,}")
_ORPHAN_PUNCT_RE = re.compile(r"\s*([,，、;；])\s*([,，、;；])+")
_TRAIL_PUNCT_RE = re.compile(r"[\s,，、;；]+$")
_LEAD_PUNCT_RE = re.compile(r"^[\s,，、;；]+")


@dataclass
class GroundingVerdict:
    ok: bool
    cited: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)
    rewrite_mode: str = "none"  # none | strip | template

    @property
    def triggered(self) -> bool:
        return not self.ok


def normalize_file_key(name: str) -> str:
    """Basename, lowercased — join key for reply cites vs tool metadata."""
    return os.path.basename(str(name or "").strip()).lower()


def extract_file_mentions(text: str) -> list[str]:
    """Return unique image-like basenames cited in ``text`` (display order)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _FILE_RE.finditer(text):
        key = normalize_file_key(m.group(1))
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def collect_allowed_files(
    tool_calls: Iterable[dict[str, Any]] | None = None,
    *,
    working_memory: Optional[dict[str, Any]] = None,
) -> set[str]:
    """Union of basenames from tool metadata and working-memory ``last_files``."""
    allowed: set[str] = set()
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        for key in ("files", "selected_keys"):
            vals = meta.get(key) or []
            if isinstance(vals, list):
                for f in vals:
                    k = normalize_file_key(str(f))
                    if k:
                        allowed.add(k)
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        for f in args.get("files") or []:
            k = normalize_file_key(str(f))
            if k:
                allowed.add(k)
    wm = working_memory or {}
    for f in wm.get("last_files") or []:
        k = normalize_file_key(str(f))
        if k:
            allowed.add(k)
    return allowed


_FILE_BEARING_TOOLS = frozenset(
    {
        "gallery_search",
        "gallery_select",
        "explain_photo",
        "export_selected",
        "recommend_film_for_photo",
        "apply_film_vibe",
    }
)


def should_enforce_groundedness(
    tool_calls: Iterable[dict[str, Any]] | None,
    *,
    working_memory: Optional[dict[str, Any]] = None,
) -> bool:
    """True when there is an allowed-file set to ground against.

    Avoids over-refusal after non-file tools (e.g. gallery_stats) with empty allowed.
    """
    allowed = collect_allowed_files(tool_calls, working_memory=working_memory)
    if allowed:
        return True
    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        if str(tc.get("tool") or "") in _FILE_BEARING_TOOLS:
            # Search/select ran but returned no files — still block invented cites.
            return True
    return False


def check_groundedness(
    reply: str,
    allowed: set[str],
) -> GroundingVerdict:
    cited = extract_file_mentions(reply)
    allowed_norm = {normalize_file_key(a) for a in allowed if normalize_file_key(a)}
    unknown = [c for c in cited if c not in allowed_norm]
    return GroundingVerdict(
        ok=not unknown,
        cited=cited,
        unknown=unknown,
        allowed=sorted(allowed_norm),
    )


def strip_ungrounded_mentions(reply: str, unknown: list[str]) -> str:
    """Remove unknown basenames; keep surrounding prose and allowed cites."""
    text = str(reply or "")
    for name in unknown:
        key = normalize_file_key(name)
        if not key:
            continue
        text = re.sub(rf"(?i)\b{re.escape(key)}\b", "", text)
    text = _ORPHAN_PUNCT_RE.sub(r"\1", text)
    text = _WS_RE.sub(" ", text)
    text = _LEAD_PUNCT_RE.sub("", text)
    text = _TRAIL_PUNCT_RE.sub("", text)
    # Collapse leftover "以及 / and / ," after removals.
    text = re.sub(r"(?i)\b(?:以及|和|and|与)\s*(?=以及|和|and|与|[,，、]|$)", "", text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def _template_reply(allowed: set[str]) -> str:
    if allowed:
        sample = ", ".join(sorted(normalize_file_key(a) for a in allowed)[:15])
        return (
            "我只依据工具结果作答，已去掉未在工具结果中出现的文件名。"
            f"本轮可引用：{sample}。"
        )
    return (
        "工具没有返回可引用的文件名，所以我不能列举具体照片。"
        "可以换个关键词或放宽筛选条件再试。"
    )


def rewrite_ungrounded_reply(
    reply: str,
    allowed: set[str],
    unknown: list[str],
) -> tuple[str, str]:
    """Return ``(rewritten_text, mode)`` where mode is ``strip`` or ``template``.

    Prefer keeping honest prose after stripping invented filenames. Use the
    template only when the remainder is too thin to stand alone.
    """
    _ = unknown  # listed for API clarity; strip uses the same set
    stripped = strip_ungrounded_mentions(reply, unknown)
    still_bad = [c for c in extract_file_mentions(stripped) if c not in {
        normalize_file_key(a) for a in allowed
    }]
    # Keep strip when prose remains or allowed cites remain.
    if stripped and not still_bad and (
        len(stripped) >= 8 or extract_file_mentions(stripped)
    ):
        return stripped, "strip"
    return _template_reply(allowed), "template"


def ground_reply(
    reply: str,
    *,
    tool_calls: Iterable[dict[str, Any]] | None = None,
    working_memory: Optional[dict[str, Any]] = None,
) -> tuple[str, GroundingVerdict]:
    """Return ``(possibly_rewritten_reply, verdict)``.

    When enforcement does not apply, verdict.ok is True and reply is unchanged.
    """
    if not should_enforce_groundedness(tool_calls, working_memory=working_memory):
        return reply, GroundingVerdict(ok=True, cited=[], unknown=[], allowed=[])
    allowed = collect_allowed_files(tool_calls, working_memory=working_memory)
    verdict = check_groundedness(reply, allowed)
    if verdict.ok:
        return reply, verdict
    rewritten, mode = rewrite_ungrounded_reply(reply, allowed, verdict.unknown)
    verdict.rewrite_mode = mode
    return rewritten, verdict
