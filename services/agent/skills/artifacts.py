"""Artifact skill: let the agent persist a text/markdown/code deliverable.

``write_artifact`` writes model-authored content to a per-session workspace directory
and returns a served URL + byte size, so a generated report / script / dataset becomes
a real, downloadable product the UI can link to (the "产物" in an AI-native agent demo).

The skill is bound per request to one ``session_dir`` (isolation) and a ``url_prefix``
used to build the link the API serves the file back from. File names are sanitized to a
safe charset and confined to the session dir (no path traversal).
"""
from __future__ import annotations

import os
import re
from typing import Any

from services.agent.skills.base import SkillResult

_MAX_BYTES = 1_000_000  # 1 MB per artifact keeps the demo workspace bounded
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_ALLOWED_EXT = (".md", ".txt", ".json", ".csv", ".py", ".html", ".log")


def sanitize_artifact_name(raw: str) -> str:
    """Reduce an arbitrary name to a safe, extension-bearing basename."""
    base = os.path.basename(str(raw or "").strip()) or "artifact.txt"
    base = _SAFE_NAME.sub("_", base).strip("._") or "artifact"
    if not base.lower().endswith(_ALLOWED_EXT):
        base = f"{base}.txt"
    return base[:120]


class WriteArtifactSkill:
    """Persist content to the session workspace and return a served URL."""

    name = "write_artifact"
    description = (
        "Save a text/markdown/code deliverable (report, script, dataset, notes) to the "
        "user's workspace and return a URL to download it. Use this to produce a concrete "
        "artifact the user can keep. Allowed extensions: .md .txt .json .csv .py .html .log"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "File name, e.g. 'report.md'."},
            "content": {"type": "string", "description": "Full file contents to write."},
        },
        "required": ["name", "content"],
        "additionalProperties": False,
    }

    def __init__(self, session_dir: str, *, url_prefix: str) -> None:
        self._dir = session_dir
        self._url_prefix = url_prefix.rstrip("/")

    def run(self, args: dict[str, Any]) -> SkillResult:
        content = args.get("content")
        if not isinstance(content, str) or not content:
            return SkillResult(ok=False, error="'content' must be a non-empty string")
        data = content.encode("utf-8")
        if len(data) > _MAX_BYTES:
            return SkillResult(ok=False, error=f"content too large ({len(data)} bytes > {_MAX_BYTES})")

        name = sanitize_artifact_name(args.get("name") or "artifact.txt")
        os.makedirs(self._dir, exist_ok=True)
        path = os.path.join(self._dir, name)
        # Confirm the resolved path stays inside the session dir (defense-in-depth).
        if os.path.commonpath([os.path.realpath(path), os.path.realpath(self._dir)]) != os.path.realpath(self._dir):
            return SkillResult(ok=False, error="invalid artifact path")
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            return SkillResult(ok=False, error=f"write failed: {exc}")

        url = f"{self._url_prefix}/{name}"
        return SkillResult(
            ok=True,
            output=f"Saved {name} ({len(data)} bytes): {url}",
            metadata={"name": name, "url": url, "bytes": len(data)},
        )
