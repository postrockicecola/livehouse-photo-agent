"""Eval protocol metadata — stamp every committed report so runs are comparable.

Seed-style contract: same labels, same decode config (temp/seed), same model id,
and recorded hardware. Call ``stamp_protocol`` before writing JSON reports.
"""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

_REPO = Path(__file__).resolve().parents[2]


def _git_sha() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _file_sha256(path: Path, *, limit_bytes: int = 2_000_000) -> Optional[str]:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        remaining = limit_bytes
        while remaining > 0:
            chunk = fh.read(min(65536, remaining))
            if not chunk:
                break
            h.update(chunk)
            remaining -= len(chunk)
    return h.hexdigest()


def _hardware_snapshot() -> dict[str, Any]:
    snap: dict[str, Any] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
    }
    try:
        import torch

        snap["torch"] = getattr(torch, "__version__", None)
        snap["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            snap["cuda_device"] = torch.cuda.get_device_name(0)
    except Exception:
        snap["torch"] = None
        snap["cuda_available"] = False
    # Apple Silicon MPS hint (no torch required).
    snap["system"] = platform.system()
    return snap


def load_model_eval_knobs(config_path: str | Path | None) -> dict[str, Any]:
    """Pull temperature / model / seed-relevant knobs from a YAML config."""
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.is_file():
        return {"config_path": str(config_path), "missing": True}
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {"config_path": str(path), "load_error": str(exc)[:200]}
    model = raw.get("model") or {}
    return {
        "config_path": str(path.as_posix()),
        "config_sha256_prefix": (_file_sha256(path) or "")[:16] or None,
        "provider": model.get("provider"),
        "model_name": model.get("model_name"),
        "temperature": model.get("temperature"),
        "num_predict": model.get("num_predict"),
        "fallback_model_name": model.get("fallback_model_name") or None,
    }


def stamp_protocol(
    report: dict[str, Any],
    *,
    labels_path: str | Path | None = None,
    predictions_path: str | Path | None = None,
    config_path: str | Path | None = None,
    seed: int | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Attach a ``protocol`` block in-place and return the report."""
    proto: dict[str, Any] = {
        "schema_version": "1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "seed": seed,
        "hardware": _hardware_snapshot(),
        "model": load_model_eval_knobs(config_path),
        "inputs": {},
    }
    if labels_path:
        lp = Path(labels_path)
        proto["inputs"]["labels"] = {
            "path": str(lp.as_posix()),
            "sha256_prefix": (_file_sha256(lp) or "")[:16] or None,
        }
    if predictions_path:
        pp = Path(predictions_path)
        proto["inputs"]["predictions"] = {
            "path": str(pp.as_posix()),
            "sha256_prefix": (_file_sha256(pp) or "")[:16] or None,
        }
    if extra:
        proto["extra"] = dict(extra)
    report["protocol"] = proto
    return report
