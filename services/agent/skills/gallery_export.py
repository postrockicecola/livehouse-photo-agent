"""Gallery export skill."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from services.agent.skills.base import SkillResult


class ExportSelectedSkill:
    name = "export_selected"
    description = (
        "Export currently selected (liked) Gallery photos: graded JPEG preview + RAW copy. "
        "Optionally pass an explicit file list; otherwise uses saved selection. Uses session "
        "vibe film when available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional explicit basenames; default = current selection.",
            },
            "use_session_vibe": {
                "type": "boolean",
                "description": "Use persisted film vibe (default true).",
            },
        },
        "additionalProperties": False,
    }

    def __init__(self, base_dir: str) -> None:
        self._base_dir = base_dir

    def run(self, args: dict[str, Any]) -> SkillResult:
        from api.gallery_routes import ExportRequest, _export_images_impl
        from utils.gallery_curation import read_gallery_curation

        files = [str(f).strip() for f in (args.get("files") or []) if str(f).strip()]
        if not files:
            cur = read_gallery_curation(self._base_dir) or {}
            files = [str(k) for k in (cur.get("selected_keys") or []) if str(k).strip()]
        if not files:
            return SkillResult(ok=False, error="没有可导出的选中照片；请先 gallery_select")

        use_vibe = True if args.get("use_session_vibe") is None else bool(args.get("use_session_vibe"))
        import os

        prev_env = os.environ.get("LIVEHOUSE_GALLERY_PREVIEWS_DIR")
        os.environ["LIVEHOUSE_GALLERY_PREVIEWS_DIR"] = str(Path(self._base_dir).expanduser().resolve())
        try:
            req = ExportRequest(images=files, use_session_vibe=use_vibe)
            result = _export_images_impl(req)
            # FastAPI may return JSONResponse
            if hasattr(result, "body"):
                import json

                payload = json.loads(result.body.decode("utf-8"))
                status = getattr(result, "status_code", 200)
                if status >= 400 or not payload.get("success", True):
                    return SkillResult(
                        ok=False,
                        error=str(payload.get("error") or payload.get("detail") or "export failed"),
                        metadata={"export": payload},
                    )
            elif isinstance(result, dict):
                payload = result
                if payload.get("success") is False:
                    return SkillResult(
                        ok=False,
                        error=str(payload.get("error") or "export failed"),
                        metadata={"export": payload},
                    )
            else:
                payload = {"raw": str(result)}

            export_dir = payload.get("export_dir") or payload.get("path") or ""
            summary = f"已导出 {len(files)} 张（含预览 JPEG 与 RAW 副本）" + (f"：{export_dir}" if export_dir else "。")
            return SkillResult(
                ok=True,
                output=summary,
                metadata={"ui_action": "export_done", "files": files, "export": payload},
            )
        except Exception as exc:
            return SkillResult(ok=False, error=f"export failed: {exc}")
        finally:
            if prev_env is None:
                os.environ.pop("LIVEHOUSE_GALLERY_PREVIEWS_DIR", None)
            else:
                os.environ["LIVEHOUSE_GALLERY_PREVIEWS_DIR"] = prev_env

