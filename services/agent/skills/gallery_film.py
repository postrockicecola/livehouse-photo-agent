"""Gallery film recommend / vibe skills."""
from __future__ import annotations

from typing import Any

from services.agent.skills.base import SkillResult
from services.agent.skills.gallery_common import (
    _load_rows,
    _resolve_photo_target,
)


class RecommendFilmForPhotoSkill:
    name = "recommend_film_for_photo"
    description = (
        "Look at one photo's analysis (tags/mood/caption) and recommend the best closed-set "
        "film variant for that frame. Use when the user asks for 最适合这张 / 自动推荐胶片感 "
        "(not when they name a style like Cinestill). Persists session_vibe for preview/export."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Target photo basename or path (preferred).",
            },
            "focus_file": {
                "type": "string",
                "description": "Gallery focus / open preview basename.",
            },
            "selected_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Current liked/selected files; used only when exactly one.",
            },
            "prompt": {
                "type": "string",
                "description": "Original user ask (for session_vibe prompt field).",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from services.film_recommend_service import find_row_by_file, recommend_film_for_row
        from services.vibe_film_policy import session_vibe_payload_from_decision
        from utils.session_vibe import read_session_vibe, write_session_vibe

        target, err = _resolve_photo_target(self._base_dir, args)
        if err or not target:
            msg = err or "missing photo target"
            return SkillResult(
                ok=False,
                error=msg,
                output=msg,
                metadata={"reply_zh": msg},
            )

        rows = _load_rows(self._base_dir)
        row = find_row_by_file(rows, target)
        if row is None:
            msg = f"在 analysis_results 中找不到「{target}」；请确认该图已完成分析"
            return SkillResult(
                ok=False,
                error=msg,
                output=msg,
                metadata={"reply_zh": msg, "focus_file": target},
            )

        prompt = str(args.get("prompt") or "").strip() or "最适合这张图的胶片感"
        decision = recommend_film_for_row(row, prompt=prompt)
        payload = session_vibe_payload_from_decision(decision)
        written = write_session_vibe(self._base_dir, payload)
        if written is None:
            return SkillResult(ok=False, error="failed to write session_vibe.json")

        vibe = read_session_vibe(self._base_dir)
        label = (vibe or {}).get("label_zh") or decision.label_zh
        variant = (vibe or {}).get("film_variant") or decision.film_variant
        intensity = (vibe or {}).get("intensity")
        if intensity is None:
            intensity = decision.intensity
        reason = (vibe or {}).get("reason_zh") or decision.reason_zh
        summary = (
            f"为「{target}」推荐「{label}」（{variant}，强度 {float(intensity):.2f}）。"
            f"{reason} "
            "请点回复下方的「打开风格预览」查看效果（不要用 Markdown 图片列表）。"
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "ui_action": "reload_vibe",
                "session_vibe": vibe,
                "decision": decision.to_json(),
                "reply_zh": summary,
                "files": [target],
                "count": 1,
                "focus_file": target,
            },
        )


class ApplyFilmVibeSkill:
    name = "apply_film_vibe"
    description = (
        "Apply a film / grade vibe to the current Gallery session from a natural-language "
        "prompt (e.g. 复古胶片, Cinestill 800T, 黑白纪实, 颜色再浓烈一些). Relative intensify "
        "keeps the current film_variant and raises intensity. Persists session_vibe for Lab "
        "preview and export."
    )
    parameters = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Style description in Chinese or English."},
            "clear": {"type": "boolean", "description": "If true, clear session vibe instead."},
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from services.vibe_film_policy import resolve_vibe_from_prompt, session_vibe_payload_from_decision
        from utils.session_vibe import clear_session_vibe, read_session_vibe, write_session_vibe

        if bool(args.get("clear")):
            clear_session_vibe(self._base_dir)
            return SkillResult(
                ok=True,
                output="已清除 session vibe。",
                metadata={"ui_action": "reload_vibe", "session_vibe": None},
            )

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return SkillResult(ok=False, error="'prompt' is required unless clear=true")

        prior = read_session_vibe(self._base_dir)
        decision = resolve_vibe_from_prompt(prompt, prior_session=prior)
        if decision.matched_by == "rules:intensity_needs_prior":
            msg = decision.reason_zh or "请先选定一种胶片风格，再说「颜色再浓烈一些」"
            return SkillResult(
                ok=False,
                error=msg,
                metadata={"reply_zh": msg, "decision": decision.to_json()},
            )
        payload = session_vibe_payload_from_decision(decision)
        written = write_session_vibe(self._base_dir, payload)
        if written is None:
            return SkillResult(ok=False, error="failed to write session_vibe.json")

        vibe = read_session_vibe(self._base_dir)
        label = (vibe or {}).get("label_zh") or decision.label_zh
        variant = (vibe or {}).get("film_variant") or decision.film_variant
        try:
            intensity = float((vibe or {}).get("intensity") if vibe else decision.intensity)
        except (TypeError, ValueError):
            intensity = float(decision.intensity)
        files: list[str] = []
        try:
            from pathlib import Path

            from utils.gallery_curation import read_gallery_curation

            cur = read_gallery_curation(self._base_dir) or {}
            # Prefer basenames for ChatDock/Gallery lookup; selected_keys may be absolute paths.
            seen: set[str] = set()
            for raw in cur.get("selected_keys") or []:
                s = str(raw or "").strip()
                if not s:
                    continue
                base = Path(s).name or s
                if base in seen:
                    continue
                seen.add(base)
                files.append(base)
        except Exception:
            files = []
        summary = (
            f"已应用风格「{label}」（{variant}，强度 {intensity:.2f}）。"
            "请点回复下方的「打开风格预览」查看效果（不要罗列文件名）。"
        )
        return SkillResult(
            ok=True,
            output=summary,
            metadata={
                "ui_action": "reload_vibe",
                "session_vibe": vibe,
                "decision": decision.to_json(),
                "reply_zh": summary,
                "files": files,
                "count": len(files),
            },
        )

