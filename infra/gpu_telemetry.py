"""Apple Silicon GPU telemetry (real ``powermetrics`` readings, no NVIDIA).

Mac mini / Apple Silicon has no ``nvidia-smi`` / ``pynvml``; the real GPU busy signal lives in
``powermetrics --samplers gpu_power`` (root-only). To keep the (non-root) API process clean, a
separate sampler process (``scripts/gpu_telemetry_sampler.py``, run with ``sudo``) parses that
stream and atomically writes the latest reading to a small JSON file. This module is the shared
parser + reader: the sampler writes, ``infra/metrics.py`` reads (best-effort, never raises).

Sample schema (JSON)::

    {"ts": 1719640000.12, "gpu_util": 0.83, "gpu_freq_mhz": 1278.0, "gpu_power_w": 7.4}

``gpu_util`` is a 0..1 fraction (``GPU HW active residency``). Readers should treat a sample older
than ``DEFAULT_MAX_AGE_SEC`` as stale (sampler not running) and fall back to the busy-time estimate.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Any

# ``GPU HW active residency:  82.34% (...)`` — also matches the older ``GPU active residency`` label.
_RESIDENCY_RE = re.compile(r"GPU\s+(?:HW\s+)?active\s+residency:\s*([\d.]+)\s*%", re.IGNORECASE)
_FREQ_RE = re.compile(r"GPU\s+(?:HW\s+)?active\s+frequency:\s*([\d.]+)\s*MHz", re.IGNORECASE)
_POWER_RE = re.compile(r"GPU\s+Power:\s*([\d.]+)\s*mW", re.IGNORECASE)

DEFAULT_MAX_AGE_SEC = 10.0


def telemetry_path() -> str:
    """Shared sampler↔reader JSON path. Override with ``LUMA_GPU_TELEMETRY_PATH``."""
    override = os.environ.get("LUMA_GPU_TELEMETRY_PATH")
    if override:
        return override
    return os.path.join(tempfile.gettempdir(), "luma_gpu_telemetry.json")


def parse_gpu_block(text: str) -> dict[str, float] | None:
    """Extract ``gpu_util`` (0..1), ``gpu_freq_mhz``, ``gpu_power_w`` from a powermetrics chunk.

    Returns ``None`` when no residency reading is present (the only field we strictly require).
    """
    m = _RESIDENCY_RE.search(text)
    if not m:
        return None
    try:
        util_pct = float(m.group(1))
    except (TypeError, ValueError):
        return None
    out: dict[str, float] = {"gpu_util": max(0.0, min(1.0, util_pct / 100.0))}
    fm = _FREQ_RE.search(text)
    if fm:
        try:
            out["gpu_freq_mhz"] = float(fm.group(1))
        except (TypeError, ValueError):
            pass
    pm = _POWER_RE.search(text)
    if pm:
        try:
            out["gpu_power_w"] = round(float(pm.group(1)) / 1000.0, 3)
        except (TypeError, ValueError):
            pass
    return out


def write_sample(sample: dict[str, float], *, path: str | None = None) -> None:
    """Atomically persist one reading (``ts`` stamped here). Best-effort; swallows IO errors."""
    p = path or telemetry_path()
    payload = {"ts": time.time(), **sample}
    tmp = f"{p}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, p)
    except OSError:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def read_latest_sample(
    *, path: str | None = None, max_age_sec: float = DEFAULT_MAX_AGE_SEC
) -> dict[str, Any] | None:
    """Latest reading + ``age_sec``, or ``None`` if missing/unparseable/stale (sampler not running)."""
    p = path or telemetry_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    ts = data.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    age = time.time() - float(ts)
    if age > float(max_age_sec) or age < -5.0:
        return None
    util = data.get("gpu_util")
    if not isinstance(util, (int, float)):
        return None
    out: dict[str, Any] = {
        "gpu_util": max(0.0, min(1.0, float(util))),
        "age_sec": round(age, 3),
    }
    if isinstance(data.get("gpu_freq_mhz"), (int, float)):
        out["gpu_freq_mhz"] = round(float(data["gpu_freq_mhz"]), 1)
    if isinstance(data.get("gpu_power_w"), (int, float)):
        out["gpu_power_w"] = round(float(data["gpu_power_w"]), 3)
    return out
