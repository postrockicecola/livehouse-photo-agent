"""Minimal ComfyUI HTTP client (local only)."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


class ComfyUIError(RuntimeError):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def ping(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=min(5.0, self.timeout))
            return r.status_code == 200
        except requests.RequestException:
            return False

    def upload_image(self, path: Path, *, subfolder: str = "", overwrite: bool = True) -> dict[str, Any]:
        with path.open("rb") as f:
            files = {"image": (path.name, f, "application/octet-stream")}
            data = {"subfolder": subfolder, "type": "input", "overwrite": "true" if overwrite else "false"}
            r = requests.post(
                f"{self.base_url}/upload/image",
                files=files,
                data=data,
                timeout=self.timeout,
            )
        if r.status_code != 200:
            raise ComfyUIError(f"upload failed: {r.status_code} {r.text[:500]}")
        out = r.json()
        if not out.get("name"):
            raise ComfyUIError(f"upload response missing name: {out}")
        return out

    def queue_prompt(self, workflow: dict[str, Any], *, client_id: str | None = None) -> str:
        cid = client_id or str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": cid}
        r = requests.post(f"{self.base_url}/prompt", json=payload, timeout=self.timeout)
        if r.status_code != 200:
            raise ComfyUIError(f"queue prompt failed: {r.status_code} {r.text[:800]}")
        data = r.json()
        pid = data.get("prompt_id")
        if not pid:
            raise ComfyUIError(f"no prompt_id in response: {data}")
        return str(pid)

    def get_history(self, prompt_id: str) -> dict[str, Any] | None:
        r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=self.timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if prompt_id in data:
            return data[prompt_id]
        return data if data else None

    def download_view(
        self,
        *,
        filename: str,
        subfolder: str = "",
        folder_type: str = "output",
        dest: Path,
    ) -> Path:
        q = urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
        r = requests.get(f"{self.base_url}/view?{q}", timeout=self.timeout)
        if r.status_code != 200:
            raise ComfyUIError(f"view download failed: {r.status_code}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        return dest


def load_workflow_template(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ComfyUIError("workflow must be a JSON object")
    return raw
