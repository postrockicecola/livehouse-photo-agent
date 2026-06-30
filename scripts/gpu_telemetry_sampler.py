#!/usr/bin/env python3
"""Apple Silicon GPU telemetry sampler — feeds the infra dashboard's real GPU utilization.

``powermetrics`` is root-only, so run this as a small standalone process and let the (non-root)
API read the JSON file it writes:

    sudo python scripts/gpu_telemetry_sampler.py            # 1s interval, default temp path
    sudo python scripts/gpu_telemetry_sampler.py -i 500     # 0.5s interval
    LUMA_GPU_TELEMETRY_PATH=/tmp/gpu.json sudo -E python scripts/gpu_telemetry_sampler.py

It streams ``powermetrics --samplers gpu_power``, parses each block, and atomically writes the
latest ``GPU HW active residency`` (+ frequency / power) to the shared telemetry file. Stop with
Ctrl-C. If the sampler is not running, the dashboard transparently falls back to the busy-time
estimate (``gpu_util_source: "estimate"``).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Allow ``python scripts/gpu_telemetry_sampler.py`` from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from infra.gpu_telemetry import parse_gpu_block, telemetry_path, write_sample  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Apple Silicon GPU telemetry sampler (root).")
    parser.add_argument(
        "-i", "--interval-ms", type=int, default=1000, help="powermetrics sample interval (ms)."
    )
    parser.add_argument(
        "-o", "--out", default=None, help="Output JSON path (default: LUMA_GPU_TELEMETRY_PATH or temp)."
    )
    args = parser.parse_args()

    out_path = args.out or telemetry_path()
    interval = max(100, int(args.interval_ms))

    if os.geteuid() != 0:
        print("error: powermetrics requires root; re-run with sudo.", file=sys.stderr)
        return 1

    cmd = [
        "powermetrics",
        "--samplers", "gpu_power",
        "-i", str(interval),
        "--format", "text",
    ]
    print(f"[gpu-telemetry] streaming powermetrics → {out_path} (interval={interval}ms)", flush=True)

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1
        )
    except FileNotFoundError:
        print("error: powermetrics not found (macOS only).", file=sys.stderr)
        return 1

    block: list[str] = []
    samples = 0
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            # powermetrics separates samples with a form-feed (\f) / blank-heavy headers; flush on
            # the GPU power line which closes each GPU block.
            block.append(line)
            if "GPU Power" in line or "\f" in line:
                parsed = parse_gpu_block("".join(block))
                block = []
                if parsed is not None:
                    write_sample(parsed, path=out_path)
                    samples += 1
                    if samples % 10 == 1:
                        util = parsed.get("gpu_util", 0.0)
                        print(
                            f"[gpu-telemetry] gpu_util={util * 100:.1f}% "
                            f"freq={parsed.get('gpu_freq_mhz', '—')}MHz "
                            f"power={parsed.get('gpu_power_w', '—')}W",
                            flush=True,
                        )
    except KeyboardInterrupt:
        print("\n[gpu-telemetry] stopping…", flush=True)
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
