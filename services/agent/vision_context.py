"""Optional vision turn context: attach a focus-file thumbnail for multimodal chat.

Used when the UI sends ``focus_file`` so recommend/explain turns can see pixels,
not only analysis JSON. Graceful no-op when the file is missing or decode fails.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_VISION_ENV = "LIVEHOUSE_AGENT_VISION"
_MAX_EDGE = 768
_JPEG_QUALITY = 85


def vision_enabled() -> bool:
    raw = (os.environ.get(_VISION_ENV) or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def resolve_preview_path(base_dir: str, focus_file: str) -> Path | None:
    name = Path(str(focus_file or "").strip()).name
    if not name:
        return None
    base = Path(base_dir).expanduser().resolve()
    for candidate in (base / name, base / str(focus_file).strip()):
        if candidate.is_file():
            return candidate
    return None


def encode_focus_thumbnail(
    base_dir: str,
    focus_file: str,
    *,
    max_edge: int = _MAX_EDGE,
) -> Optional[dict[str, str]]:
    """Return ``{media_type, data_base64, file_name}`` or ``None``."""
    if not vision_enabled():
        return None
    path = resolve_preview_path(base_dir, focus_file)
    if path is None:
        return None
    try:
        from PIL import Image
        import io

        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((max_edge, max_edge))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=_JPEG_QUALITY, optimize=True)
            raw = buf.getvalue()
        return {
            "media_type": "image/jpeg",
            "data_base64": base64.b64encode(raw).decode("ascii"),
            "file_name": path.name,
        }
    except Exception:
        logger.debug("encode_focus_thumbnail failed for %s", path, exc_info=True)
        return None


def multimodal_user_content(
    text: str,
    *,
    image: Optional[dict[str, str]] = None,
) -> str | list[dict[str, Any]]:
    """Build OpenAI/Ollama-compatible user content (str or content parts)."""
    if not image or not image.get("data_base64"):
        return text
    data_url = f"data:{image.get('media_type') or 'image/jpeg'};base64,{image['data_base64']}"
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]


def ollama_images_from_content(content: Any) -> list[str]:
    """Extract raw base64 images from multimodal content parts for Ollama ``images``."""
    if not isinstance(content, list):
        return []
    out: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            url = ""
            iu = part.get("image_url")
            if isinstance(iu, dict):
                url = str(iu.get("url") or "")
            elif isinstance(iu, str):
                url = iu
            if "base64," in url:
                out.append(url.split("base64,", 1)[1])
    return out


def flatten_text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "".join(parts)
    return str(content or "")
