#!/usr/bin/env python3
"""Serial vs concurrent VLM batch demo — make the "压榨算力" speedup visible.

Runs the **same** batch of images through the real inference stack twice:

* **serial**     — ``num_workers=1``, submitted one-by-one (the naive baseline)
* **concurrent** — ``num_workers=K``, submitted via a thread pool (GPU stays busy)

While each phase runs, it samples GPU utilization (real Apple-Silicon ``powermetrics`` reading when
``scripts/gpu_telemetry_sampler.py`` is running, otherwise the busy-time estimate) and records a
timeline. It then emits a JSON document + a dependency-free SVG putting the two GPU curves and total
times side by side, plus the speedup factor — the money shot for the demo.

Examples::

    # real Ollama (start the GPU sampler first for real readings):
    sudo python scripts/gpu_telemetry_sampler.py &
    python scripts/gpu_pressure_demo.py --images-dir /path/to/jpegs --count 24 --workers 4

    # no model handy? shape-preview with injected latency (GPU = busy-time estimate):
    python scripts/gpu_pressure_demo.py --simulate --count 24 --workers 4
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from inference.client import InferenceClient, build_inference_router_from_model_config
from inference.providers.base import InferenceProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse
from infra.metrics import inference_queue_runtime_snapshot
from utils.config_loader import ConfigLoader

DEMO_SCHEMA_VERSION = "1"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


class _SimulatedProvider(InferenceProvider):
    """Latency-injecting provider so the demo runs (and the SVG has shape) without a real model."""

    PROVIDER_ID = "demo_sim"

    def __init__(self, *, min_latency_ms: int, max_latency_ms: int) -> None:
        self.min_latency_ms = max(1, int(min_latency_ms))
        self.max_latency_ms = max(self.min_latency_ms, int(max_latency_ms))

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        time.sleep(random.randint(self.min_latency_ms, self.max_latency_ms) / 1000.0)
        return InferenceResponse(status="success", text='{"score": 7.5}', model=model_name)


@dataclass
class _PhaseResult:
    name: str
    workers: int
    concurrency: int
    images: int
    total_sec: float
    throughput_img_per_sec: float
    success: int
    errors: int
    latency_p50_ms: int
    latency_p95_ms: int
    gpu_avg: float | None
    gpu_peak: float | None
    gpu_source: str
    samples: list[dict[str, float]] = field(default_factory=list)


class _GpuSampler:
    """Background thread sampling GPU utilization, stamped relative to the active phase start."""

    def __init__(self, interval_ms: int) -> None:
        self._interval = max(0.05, interval_ms / 1000.0)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._active = False
        self._t0 = 0.0
        self._samples: list[dict[str, float]] = []
        self._sources: set[str] = set()
        self._thread = threading.Thread(target=self._loop, name="gpu-demo-sampler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def begin_phase(self) -> None:
        with self._lock:
            self._active = True
            self._t0 = time.perf_counter()
            self._samples = []
            self._sources = set()

    def end_phase(self) -> tuple[list[dict[str, float]], str]:
        with self._lock:
            self._active = False
            samples = list(self._samples)
            src = "+".join(sorted(self._sources)) if self._sources else "none"
        return samples, src

    @staticmethod
    def _read() -> tuple[float | None, str]:
        snap = inference_queue_runtime_snapshot()
        util = snap.get("gpu_util")
        src = snap.get("gpu_util_source") or "none"
        return (float(util) if isinstance(util, (int, float)) else None, str(src))

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if not self._active:
                    continue
                t0 = self._t0
            util, src = self._read()
            if util is None:
                continue
            with self._lock:
                if not self._active:
                    continue
                self._samples.append({"t": round(time.perf_counter() - t0, 3), "util": round(util, 4)})
                self._sources.add(src)


def discover_images(images_dir: str | None, count: int, *, simulate: bool) -> list[str]:
    if images_dir:
        root = Path(images_dir).expanduser()
        if not root.is_dir():
            raise SystemExit(f"--images-dir not a directory: {root}")
        found = sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTS and p.is_file())
        if not found and not simulate:
            raise SystemExit(f"No images ({sorted(IMAGE_EXTS)}) under {root}")
        paths = [str(p) for p in found[: max(1, count)]]
        if paths:
            return paths
    if not simulate:
        raise SystemExit("Provide --images-dir with real images, or use --simulate for a dry run.")
    return [f"/tmp/gpu-demo-sim-{i}.jpg" for i in range(max(1, count))]


def _percentile(values: list[float], p: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    q = statistics.quantiles(values, n=100, method="inclusive")
    return int(q[max(0, min(98, int(p) - 1))])


def _build_client(*, simulate: bool, model_config: dict[str, Any], workers: int, num_predict: int) -> InferenceClient:
    if simulate:
        sim = _SimulatedProvider(min_latency_ms=180, max_latency_ms=420)
        router = InferenceRouter(primary_provider=sim, primary_model_name="demo-sim")
        return InferenceClient(router=router, num_workers=workers, max_retries=0, timeout=30)
    router = build_inference_router_from_model_config(model_config)
    return InferenceClient(
        router=router,
        queue_wait_timeout_seconds=float(model_config.get("queue_wait_timeout_seconds", 120)),
        num_workers=workers,
        max_retries=int(model_config.get("max_retries", 2) or 2),
        timeout=int(model_config.get("timeout", 120) or 120),
        max_queue_size=max(workers, int(model_config.get("max_inference_queue_size", workers))),
    )


def run_phase(
    *,
    name: str,
    client: InferenceClient,
    image_paths: list[str],
    prompt: str,
    num_predict: int,
    workers: int,
    concurrency: int,
    sampler: _GpuSampler,
) -> _PhaseResult:
    lat_ms: list[float] = []
    success = 0
    errors = 0

    def one(i: int, path: str) -> tuple[dict[str, Any], float]:
        start = time.perf_counter()
        out = client.predict(
            image_path=path,
            prompt=prompt,
            priority=0,
            trace_id=f"gpu-demo-{name}-{i}",
            inference_extra_metadata={"num_predict": num_predict},
        )
        return out, (time.perf_counter() - start) * 1000.0

    sampler.begin_phase()
    t0 = time.perf_counter()
    if concurrency <= 1:
        for i, path in enumerate(image_paths):
            out, ms = one(i, path)
            lat_ms.append(ms)
            success += 1 if str(out.get("status")).lower() in ("success", "degraded") else 0
            errors += 0 if str(out.get("status")).lower() in ("success", "degraded") else 1
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(one, i, p) for i, p in enumerate(image_paths)]
            for fut in as_completed(futures):
                out, ms = fut.result()
                lat_ms.append(ms)
                success += 1 if str(out.get("status")).lower() in ("success", "degraded") else 0
                errors += 0 if str(out.get("status")).lower() in ("success", "degraded") else 1
    total_sec = max(1e-6, time.perf_counter() - t0)
    samples, src = sampler.end_phase()

    utils = [s["util"] for s in samples]
    return _PhaseResult(
        name=name,
        workers=workers,
        concurrency=concurrency,
        images=len(image_paths),
        total_sec=round(total_sec, 3),
        throughput_img_per_sec=round(len(image_paths) / total_sec, 3),
        success=success,
        errors=errors,
        latency_p50_ms=_percentile(lat_ms, 50),
        latency_p95_ms=_percentile(lat_ms, 95),
        gpu_avg=round(sum(utils) / len(utils), 4) if utils else None,
        gpu_peak=round(max(utils), 4) if utils else None,
        gpu_source=src,
        samples=samples,
    )


def build_document(*, args: argparse.Namespace, phases: list[_PhaseResult]) -> dict[str, Any]:
    serial = next((p for p in phases if p.name == "serial"), None)
    concurrent = next((p for p in phases if p.name == "concurrent"), None)
    speedup = (
        round(serial.total_sec / concurrent.total_sec, 2)
        if serial and concurrent and concurrent.total_sec > 0
        else None
    )
    return {
        "schema_version": DEMO_SCHEMA_VERSION,
        "tool": "gpu_pressure_demo",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "simulate": bool(args.simulate),
            "images": phases[0].images if phases else 0,
            "workers": int(args.workers),
            "num_predict": int(args.num_predict),
            "sample_interval_ms": int(args.sample_interval_ms),
        },
        "summary": {
            "speedup_x": speedup,
            "serial_total_sec": serial.total_sec if serial else None,
            "concurrent_total_sec": concurrent.total_sec if concurrent else None,
            "serial_gpu_avg": serial.gpu_avg if serial else None,
            "concurrent_gpu_avg": concurrent.gpu_avg if concurrent else None,
            "gpu_source": (concurrent or serial).gpu_source if phases else "none",
        },
        "phases": [asdict(p) for p in phases],
    }


def render_svg(doc: dict[str, Any]) -> str:
    """Dependency-free SVG: overlaid GPU-util curves + total-time bars + speedup callout."""
    phases = doc.get("phases") or []
    if not phases:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='40'></svg>"
    W, H = 820, 460
    pad_l, pad_r, pad_t, pad_b = 64, 220, 56, 56
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    max_t = max((s["t"] for p in phases for s in p["samples"]), default=1.0) or 1.0
    colors = {"serial": "#9ca3af", "concurrent": "#10b981"}

    def x(t: float) -> float:
        return pad_l + plot_w * (t / (max_t * 1.02))

    def y(util: float) -> float:
        return pad_t + plot_h * (1 - max(0.0, min(1.0, util)))

    summary = doc.get("summary") or {}
    src = summary.get("gpu_source", "none")
    speedup = summary.get("speedup_x")
    title = "Serial vs Concurrent VLM Batch — GPU Saturation"
    parts: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' font-family='sans-serif'>",
        f"<rect width='{W}' height='{H}' fill='#0b0b0c'/>",
        f"<text x='{pad_l}' y='28' font-size='16' font-weight='bold' fill='#e5e7eb'>{title}</text>",
        f"<text x='{pad_l}' y='46' font-size='11' fill='#9ca3af'>GPU source: {src} · y=GPU util % · x=seconds</text>",
        # axes
        f"<line x1='{pad_l}' y1='{pad_t+plot_h}' x2='{pad_l+plot_w}' y2='{pad_t+plot_h}' stroke='#3f3f46'/>",
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{pad_t+plot_h}' stroke='#3f3f46'/>",
    ]
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = pad_t + plot_h * (1 - frac)
        parts.append(f"<line x1='{pad_l}' y1='{gy:.1f}' x2='{pad_l+plot_w}' y2='{gy:.1f}' stroke='#1f1f23'/>")
        parts.append(
            f"<text x='{pad_l-8}' y='{gy+4:.1f}' text-anchor='end' font-size='10' fill='#6b7280'>{int(frac*100)}</text>"
        )

    for p in phases:
        color = colors.get(p["name"], "#60a5fa")
        samples = p["samples"]
        if samples:
            pts = " ".join(f"{x(s['t']):.1f},{y(s['util']):.1f}" for s in samples)
            parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2.5'/>")
        avg = p.get("gpu_avg")
        if avg is not None:
            ay = y(avg)
            parts.append(
                f"<line x1='{pad_l}' y1='{ay:.1f}' x2='{pad_l+plot_w}' y2='{ay:.1f}' "
                f"stroke='{color}' stroke-width='1' stroke-dasharray='4 4' opacity='0.5'/>"
            )

    # Right-side info panel: per-phase totals + speedup.
    ix = pad_l + plot_w + 24
    iy = pad_t + 6
    parts.append(f"<text x='{ix}' y='{iy}' font-size='12' font-weight='bold' fill='#e5e7eb'>Results</text>")
    iy += 22
    for p in phases:
        color = colors.get(p["name"], "#60a5fa")
        parts.append(f"<rect x='{ix}' y='{iy-10}' width='11' height='11' rx='2' fill='{color}'/>")
        parts.append(f"<text x='{ix+18}' y='{iy}' font-size='12' fill='#e5e7eb'>{p['name']} (w={p['workers']})</text>")
        iy += 18
        gpu_avg_txt = f"{p['gpu_avg']*100:.0f}%" if p.get("gpu_avg") is not None else "—"
        gpu_peak_txt = f"{p['gpu_peak']*100:.0f}%" if p.get("gpu_peak") is not None else "—"
        parts.append(
            f"<text x='{ix+18}' y='{iy}' font-size='11' fill='#9ca3af'>"
            f"{p['total_sec']:.1f}s · {p['throughput_img_per_sec']:.2f} img/s</text>"
        )
        iy += 16
        parts.append(
            f"<text x='{ix+18}' y='{iy}' font-size='11' fill='#9ca3af'>GPU avg {gpu_avg_txt} · peak {gpu_peak_txt}</text>"
        )
        iy += 24

    if speedup is not None:
        parts.append(f"<text x='{ix}' y='{iy+8}' font-size='13' fill='#e5e7eb'>Speedup</text>")
        parts.append(
            f"<text x='{ix}' y='{iy+44}' font-size='34' font-weight='bold' fill='#10b981'>{speedup:.2f}×</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serial vs concurrent VLM batch GPU-pressure demo.")
    parser.add_argument("--images-dir", default="", help="Folder of images (recursive). Required unless --simulate.")
    parser.add_argument("--count", type=int, default=24, help="Max images to use from the folder.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent-phase worker count (GPU admission).")
    parser.add_argument("--num-predict", type=int, default=64, help="Max tokens per call (small = snappy demo).")
    parser.add_argument("--prompt", default="Rate this livehouse photo 0-10. Reply with a number.", help="VLM prompt.")
    parser.add_argument("--simulate", action="store_true", help="No model: inject latency; GPU = busy-time estimate.")
    parser.add_argument("--config", default="", help="Path to livehouse.yaml (default: repo config).")
    parser.add_argument("--sample-interval-ms", type=int, default=250, help="GPU sampling interval.")
    parser.add_argument("--output-json", default="", help="Write JSON document here (default: reports/gpu_demo/).")
    parser.add_argument("--output-svg", default="", help="Write SVG chart here (default: reports/gpu_demo/).")
    args = parser.parse_args()

    config = ConfigLoader.load(args.config or None)
    model_config = ConfigLoader.get_model_config(config)
    image_paths = discover_images(args.images_dir or None, args.count, simulate=args.simulate)
    workers = max(1, int(args.workers))

    print(
        f"[gpu-demo] images={len(image_paths)} workers={workers} "
        f"provider={'simulate' if args.simulate else model_config.get('provider', 'ollama')} "
        f"num_predict={args.num_predict}",
        flush=True,
    )

    sampler = _GpuSampler(args.sample_interval_ms)
    sampler.start()
    phases: list[_PhaseResult] = []
    try:
        for name, w, conc in (("serial", 1, 1), ("concurrent", workers, workers)):
            client = _build_client(simulate=args.simulate, model_config=model_config, workers=w, num_predict=args.num_predict)
            print(f"[gpu-demo] running phase '{name}' (workers={w})…", flush=True)
            try:
                phases.append(
                    run_phase(
                        name=name,
                        client=client,
                        image_paths=image_paths,
                        prompt=args.prompt,
                        num_predict=args.num_predict,
                        workers=w,
                        concurrency=conc,
                        sampler=sampler,
                    )
                )
            finally:
                try:
                    client._queue.shutdown()
                except Exception:
                    pass
            time.sleep(1.0)  # brief cooldown so phase curves don't bleed together
    finally:
        sampler.stop()

    doc = build_document(args=args, phases=phases)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = Path(args.output_json) if args.output_json else _ROOT / "reports" / "gpu_demo" / f"demo_{ts}.json"
    out_svg = Path(args.output_svg) if args.output_svg else _ROOT / "reports" / "gpu_demo" / f"demo_{ts}.svg"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_svg.write_text(render_svg(doc) + "\n", encoding="utf-8")

    s = doc["summary"]
    print("")
    print("# GPU Pressure Demo")
    print(f"phase      | workers | total(s) | img/s | gpu_avg | gpu_peak | p50/p95 ms")
    for p in phases:
        ga = f"{p.gpu_avg*100:.0f}%" if p.gpu_avg is not None else "—"
        gp = f"{p.gpu_peak*100:.0f}%" if p.gpu_peak is not None else "—"
        print(
            f"{p.name:<10} | {p.workers:>7} | {p.total_sec:>8.1f} | {p.throughput_img_per_sec:>5.2f} | "
            f"{ga:>7} | {gp:>8} | {p.latency_p50_ms}/{p.latency_p95_ms}"
        )
    if s["speedup_x"] is not None:
        print(f"\nSpeedup: {s['speedup_x']:.2f}×  (GPU source: {s['gpu_source']})")
    print(f"\nJSON → {out_json}")
    print(f"SVG  → {out_svg}")


if __name__ == "__main__":
    main()
