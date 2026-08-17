"""Final-answer groundedness: cite tool files via ``{ref_N}``, never invent names.

Primary contract (structural):
  1. Final-answer prompts tell the model to cite only ``{ref_0}``, ``{ref_1}``, …
  2. :func:`resolve_ref_placeholders` swaps those tokens for real basenames from
     tool ``metadata.files`` / working-memory ``last_files``.
  3. :func:`ground_reply` is a dual-insurance gate: mixed invented cites strip
     the unknown names and keep grounded prose; all-unknown or empty leftover
     text uses a deterministic template.

Event payloads use the same shape as guardrails (``type`` / ``kind`` / ``triggered``
/ ``matches`` / ``detail``) so review-queue export stays compatible.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# Concert / gallery outputs we actually serve; keep the list tight to limit FPs.
_FILE_RE = re.compile(
    r"(?i)\b([A-Za-z0-9][A-Za-z0-9._-]{0,180}\."
    r"(?:jpe?g|png|webp|gif|arw|cr2|nef|dng|heic|tiff?))\b"
)
# Placeholders the final-answer model is allowed to emit: {ref_0}, {ref_1}, …
REF_PLACEHOLDER_RE = re.compile(r"\{ref_(\d+)\}")

_GROUNDING_FAILURE_TEMPLATE = "根据搜索结果，找到了以下照片：{file_list}"
_GROUNDING_FAILURE_EMPTY = (
    "工具没有返回可引用的文件名，所以我不能列举具体照片。"
    "可以换个关键词或放宽筛选条件再试。"
)

_FILE_BEARING_TOOLS = frozenset(
    {
        "archive_search",
        "gallery_search",
        "gallery_select",
        "explain_photo",
        "export_selected",
        "recommend_film_for_photo",
        "apply_film_vibe",
        "poll_curation_job",
    }
)


@dataclass
class GroundingVerdict:
    ok: bool
    cited: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    allowed: list[str] = field(default_factory=list)
    rewrite_mode: str = "none"  # none | strip | template
    reason: str = ""  # invented_file | oob_ref | ""

    @property
    def triggered(self) -> bool:
        return not self.ok


@dataclass
class RefResolveVerdict:
    """Outcome of swapping ``{ref_N}`` placeholders for real basenames."""

    ok: bool
    refs_used: list[int] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)  # out-of-range indices

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


def ordered_ref_files(
    tool_calls: Iterable[dict[str, Any]] | None = None,
    *,
    working_memory: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Stable display basenames for ``{ref_N}`` → file mapping (first-seen order)."""
    seen: set[str] = set()
    out: list[str] = []

    def _add(raw: Any) -> None:
        base = os.path.basename(str(raw or "").strip())
        if not base:
            return
        key = base.lower()
        if key in seen:
            return
        seen.add(key)
        out.append(base)

    for tc in tool_calls or []:
        if not isinstance(tc, dict):
            continue
        meta = tc.get("metadata") if isinstance(tc.get("metadata"), dict) else {}
        for key in ("files", "selected_keys"):
            vals = meta.get(key) or []
            if isinstance(vals, list):
                for f in vals:
                    _add(f)
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        for f in args.get("files") or []:
            _add(f)
    wm = working_memory or {}
    for f in wm.get("last_files") or []:
        _add(f)
    return out


def collect_allowed_files(
    tool_calls: Iterable[dict[str, Any]] | None = None,
    *,
    working_memory: Optional[dict[str, Any]] = None,
) -> set[str]:
    """Union of basenames from tool metadata and working-memory ``last_files``."""
    return {normalize_file_key(f) for f in ordered_ref_files(tool_calls, working_memory=working_memory)}


def format_ref_index_block(files: list[str]) -> str:
    """Prompt block listing placeholders the model may cite."""
    if not files:
        return ""
    lines = [
        "PHOTO REFS — cite photos ONLY as {ref_0}, {ref_1}, … placeholders.",
        "Never write real filenames (no .jpg / .ARW names). Code will substitute them.",
        "Example: 这几张照片里 {ref_0} 构图最好，{ref_1} 是抓拍瞬间。",
        "Index:",
    ]
    for i, name in enumerate(files[:40]):
        lines.append(f"  {{ref_{i}}} → photo #{i + 1}")
    return "\n".join(lines)


def redact_filenames_to_refs(text: str, files: list[str]) -> str:
    """Replace known basenames in tool-result text with ``{ref_N}`` (longest first)."""
    out = str(text or "")
    indexed = sorted(
        ((i, os.path.basename(f)) for i, f in enumerate(files) if os.path.basename(f)),
        key=lambda t: len(t[1]),
        reverse=True,
    )
    for i, base in indexed:
        out = re.sub(re.escape(base), f"{{ref_{i}}}", out, flags=re.IGNORECASE)
    return out


def resolve_ref_placeholders(
    text: str,
    files: list[str],
) -> tuple[str, RefResolveVerdict]:
    """Replace ``{ref_N}`` with ``files[N]`` basename.

    Out-of-range refs leave the text unchanged and mark ``ok=False`` so callers
    can fall back to :func:`grounding_failure_template`.
    """
    raw = str(text or "")
    used = [int(m.group(1)) for m in REF_PLACEHOLDER_RE.finditer(raw)]
    if not used:
        return raw, RefResolveVerdict(ok=True, refs_used=[], unresolved=[])
    oob = sorted({i for i in used if i < 0 or i >= len(files)})
    if oob:
        return raw, RefResolveVerdict(ok=False, refs_used=used, unresolved=oob)

    def _sub(m: re.Match[str]) -> str:
        return os.path.basename(files[int(m.group(1))])

    return REF_PLACEHOLDER_RE.sub(_sub, raw), RefResolveVerdict(
        ok=True, refs_used=used, unresolved=[]
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
        reason="invented_file" if unknown else "",
    )


def grounding_failure_template(files: Iterable[str] | None = None) -> str:
    """Deterministic fallback — never stitch broken prose after a grounding miss."""
    names: list[str] = []
    seen: set[str] = set()
    for f in files or []:
        base = os.path.basename(str(f or "").strip())
        key = base.lower()
        if not base or key in seen:
            continue
        seen.add(key)
        names.append(base)
        if len(names) >= 15:
            break
    if names:
        return _GROUNDING_FAILURE_TEMPLATE.format(file_list=", ".join(names))
    return _GROUNDING_FAILURE_EMPTY


def _strip_unknown_mentions(reply: str, unknown: list[str]) -> str:
    """Drop invented basenames and collapse leftover separators."""
    out = str(reply or "")
    for name in sorted({str(n) for n in unknown if str(n).strip()}, key=len, reverse=True):
        out = re.sub(re.escape(name), "", out, flags=re.IGNORECASE)
    out = re.sub(r"(?:、|,|，|和|以及)\s*(?:、|,|，|和|以及)", "、", out)
    out = re.sub(r"(?:推荐|看看)\s*[、,，]\s*", "推荐 ", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip(" 、,，")


def rewrite_ungrounded_reply(
    reply: str,
    allowed: set[str],
    unknown: list[str],
) -> tuple[str, str]:
    """Keep grounded prose when possible; fall back to the deterministic template.

    Mixed cites (real + invented) strip the unknown names. All-unknown or empty
    leftover text still uses :func:`grounding_failure_template`.
    """
    allowed_norm = {normalize_file_key(a) for a in allowed if normalize_file_key(a)}
    stripped = _strip_unknown_mentions(reply, unknown)
    leftover = [c for c in extract_file_mentions(stripped) if c not in allowed_norm]
    if leftover or len(stripped) < 8:
        return grounding_failure_template(allowed), "template"
    return stripped, "strip"


def ground_reply(
    reply: str,
    *,
    tool_calls: Iterable[dict[str, Any]] | None = None,
    working_memory: Optional[dict[str, Any]] = None,
) -> tuple[str, GroundingVerdict]:
    """Return ``(possibly_rewritten_reply, verdict)``.

    On failure always uses :func:`grounding_failure_template` (no strip mode).
    When enforcement does not apply, verdict.ok is True and reply is unchanged.
    """
    if not should_enforce_groundedness(tool_calls, working_memory=working_memory):
        return reply, GroundingVerdict(ok=True, cited=[], unknown=[], allowed=[])
    ref_files = ordered_ref_files(tool_calls, working_memory=working_memory)
    allowed = {normalize_file_key(f) for f in ref_files}
    verdict = check_groundedness(reply, allowed)
    if verdict.ok:
        # Also reject leftover unresolved placeholders (should be rare post-resolve).
        leftover = [int(m.group(1)) for m in REF_PLACEHOLDER_RE.finditer(reply)]
        if leftover:
            verdict = GroundingVerdict(
                ok=False,
                cited=verdict.cited,
                unknown=verdict.unknown,
                allowed=sorted(allowed),
                rewrite_mode="template",
                reason="oob_ref",
            )
            return grounding_failure_template(ref_files), verdict
        return reply, verdict
    rewritten, mode = rewrite_ungrounded_reply(reply, allowed, verdict.unknown)
    verdict.rewrite_mode = mode
    verdict.reason = verdict.reason or "invented_file"
    if mode == "template":
        return grounding_failure_template(ref_files or sorted(allowed)), verdict
    return rewritten, verdict


def build_grounding_event(
    *,
    reason: str,
    user_text: str,
    raw_model_output: str,
    final_reply: str,
    verdict: GroundingVerdict | None = None,
    unresolved_refs: Optional[list[int]] = None,
) -> dict[str, Any]:
    """Guardrail-shaped event for review-queue / agent_events persistence."""
    v = verdict or GroundingVerdict(ok=False, rewrite_mode="template", reason=reason)
    matches = [reason] if reason else ["groundedness"]
    if unresolved_refs:
        matches = ["oob_ref"]
    return {
        "type": "grounding_violation",
        "kind": "groundedness",
        "triggered": True,
        "matches": matches,
        "detail": {
            "user_text": str(user_text or ""),
            "raw_model_output": str(raw_model_output or ""),
            "final_reply": str(final_reply or ""),
            "unknown": list(v.unknown),
            "cited": list(v.cited),
            "allowed": list(v.allowed),
            "unresolved_refs": list(unresolved_refs or []),
            "rewrite_mode": "template",
            "reason": reason or v.reason or "groundedness",
            "ts": time.time(),
        },
        # Flat mirrors kept for older review_signals / tests that read top-level keys.
        "unknown": list(v.unknown),
        "cited": list(v.cited),
        "allowed": list(v.allowed),
        "rewrite_mode": "template",
        "user_text": str(user_text or ""),
        "raw_model_output": str(raw_model_output or ""),
        "final_reply": str(final_reply or ""),
    }
