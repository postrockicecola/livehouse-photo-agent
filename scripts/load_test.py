#!/usr/bin/env python3
"""
Real-provider inference load test: concurrency sweep with latency percentiles, throughput,
token throughput (tokens/sec), and cost-per-inference attribution.

Unlike ``scripts/benchmark_inference.py`` (always simulated), this drives the *real*
``InferenceClient`` queue against a live Ollama endpoint so the numbers map to actual VLM
serving. Use ``--simulate`` to run the identical measurement path without a GPU/Ollama
(deterministic latency + synthetic token usage) — handy for CI and for validating the harness.

Outputs (any combination):
  - stdout table (default)
  - ``--output-json PATH``  machine-readable run (stable keys; dashboards / git diffs)
  - ``--output-md PATH``    Markdown table for docs / portfolio
  - ``--output-svg PATH``   dependency-free SVG chart (throughput + latency percentiles)

Examples
--------
# Real Ollama, sweep worker parallelism, write all artifacts
python scripts/load_test.py \
    --endpoint http://localhost:11434 --model llava:7b \
    --image data/eval/images/sample.jpg --requests 60 --concurrency 1,2,4 \
    --output-json reports/loadtest/llava7b.json \
    --output-md reports/loadtest/llava7b.md \
    --output-svg reports/loadtest/llava7b.svg

# No GPU needed (harness smoke / CI)
python scripts/load_test.py --simulate --requests 40 --concurrency 1,2,4
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from inference.client import InferenceClient
from inference.providers.base import InferenceProvider
from inference.providers.ollama import OllamaProvider
from inference.providers.vllm import VLLMProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse

LOADTEST_SCHEMA_VERSION = "1"


# --------------------------------------------------------------------------------------
# Simulated provider (so the full queue/token/cost path runs without a GPU)
# --------------------------------------------------------------------------------------
class SimulatedTokenProvider(InferenceProvider):
    """Sleeps a per-request latency and reports Ollama-shaped token usage."""

    PROVIDER_ID = "loadtest_simulated"

    def __init__(
        self,
        *,
        min_latency_ms: int,
        max_latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
        error_rate: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.min_latency_ms = max(1, int(min_latency_ms))
        self.max_latency_ms = max(self.min_latency_ms, int(max_latency_ms))
        self.prompt_tokens = max(0, int(prompt_tokens))
        self.completion_tokens = max(1, int(completion_tokens))
        self.error_rate = max(0.0, min(1.0, float(error_rate)))
        self._rng = random.Random(seed)

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        latency_ms = self._rng.randint(self.min_latency_ms, self.max_latency_ms)
        time.sleep(latency_ms / 1000.0)
        if self._rng.random() < self.error_rate:
            return InferenceResponse(status="error", error="simulated error", model=model_name)
        # ~70% of wall time is decode; mirror Ollama nanosecond duration fields.
        eval_ns = int(latency_ms * 0.7 * 1_000_000)
        prompt_ns = int(latency_ms * 0.3 * 1_000_000)
        return InferenceResponse(
            status="success",
            text='{"overall": 7.4, "comment": "simulated"}',
            model=model_name,
            metadata={
                "prompt_eval_count": self.prompt_tokens,
                "eval_count": self.completion_tokens,
                "prompt_eval_duration": prompt_ns,
                "eval_duration": eval_ns,
            },
        )


# --------------------------------------------------------------------------------------
# Result shapes
# --------------------------------------------------------------------------------------
@dataclass
class ScenarioResult:
    concurrency: int
    requests: int
    total_sec: float
    throughput_rps: float
    success: int
    errors: int
    latency_p50_ms: int
    latency_p95_ms: int
    latency_p99_ms: int
    latency_mean_ms: int
    # Token accounting (sums across successful requests with usage reported).
    prompt_tokens: int
    completion_tokens: int
    decode_tokens_per_sec: float
    avg_completion_tokens: float
    # Cost attribution (whichever model(s) the operator priced; 0 when not configured).
    est_cost_per_1k_usd: float
    cost_basis: str


@dataclass
class _RequestSample:
    ok: bool
    e2e_ms: float
    prompt_tokens: int | None
    completion_tokens: int | None
    eval_duration_sec: float | None
    degraded: bool


@dataclass
class CostModel:
    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0
    gpu_hourly_usd: float = 0.0

    def per_1k(self, samples: list[_RequestSample], throughput_rps: float) -> tuple[float, str]:
        ok = [s for s in samples if s.ok]
        if self.gpu_hourly_usd > 0.0:
            # Self-hosted: amortize wall-clock GPU time over completed inferences.
            if throughput_rps <= 0:
                return 0.0, "gpu_hourly"
            cost = (1000.0 / throughput_rps / 3600.0) * self.gpu_hourly_usd
            return round(cost, 6), "gpu_hourly"
        in_tok = sum((s.prompt_tokens or 0) for s in ok)
        out_tok = sum((s.completion_tokens or 0) for s in ok)
        if (self.input_usd_per_mtok > 0 or self.output_usd_per_mtok > 0) and ok:
            total = (in_tok / 1e6) * self.input_usd_per_mtok + (out_tok / 1e6) * self.output_usd_per_mtok
            return round((total / len(ok)) * 1000.0, 6), "token_pricing"
        return 0.0, "unpriced"


def _percentile(values: list[float], p: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    q = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(0, min(98, int(round(p)) - 1))
    return int(q[idx])


# --------------------------------------------------------------------------------------
# Core measurement
# --------------------------------------------------------------------------------------
def _resolve_provider_kind(args: argparse.Namespace) -> str:
    if args.simulate:
        return "simulate"
    return str(args.provider or "ollama").strip().lower()


def _build_provider(args: argparse.Namespace) -> InferenceProvider:
    kind = _resolve_provider_kind(args)
    if kind == "simulate":
        return SimulatedTokenProvider(
            min_latency_ms=int(args.sim_latency_ms * 0.7),
            max_latency_ms=int(args.sim_latency_ms * 1.3),
            prompt_tokens=args.sim_prompt_tokens,
            completion_tokens=args.sim_completion_tokens,
            error_rate=args.sim_error_rate,
            seed=args.seed,
        )
    if kind in ("vllm", "openai"):
        return VLLMProvider(
            endpoint=args.endpoint,
            temperature=args.temperature,
            num_predict=args.num_predict,
            timeout=args.timeout,
            max_retries=args.max_retries,
            retry_delay=0.5,
            api_key=args.api_key or None,
        )
    return OllamaProvider(
        endpoint=args.endpoint,
        temperature=args.temperature,
        num_predict=args.num_predict,
        timeout=args.timeout,
        max_retries=args.max_retries,
        retry_delay=0.5,
    )


def run_scenario(
    *,
    concurrency: int,
    requests: int,
    provider: InferenceProvider,
    model_name: str,
    image_path: str,
    prompt: str,
    queue_wait_timeout_seconds: float,
    timeout: int,
    cost_model: CostModel,
) -> ScenarioResult:
    router = InferenceRouter(primary_provider=provider, primary_model_name=model_name)
    client = InferenceClient(
        router=router,
        queue_wait_timeout_seconds=queue_wait_timeout_seconds,
        num_workers=concurrency,
        timeout=timeout,
        max_retries=0,
        max_queue_size=max(16, requests),
    )

    samples: list[_RequestSample] = []

    def one_request(i: int) -> _RequestSample:
        start = time.perf_counter()
        out = client.predict(
            image_path=image_path,
            prompt=prompt,
            priority=0,
            trace_id=f"loadtest-{concurrency}-{i}",
        )
        e2e_ms = (time.perf_counter() - start) * 1000.0
        meta = dict(out.get("metadata") or {})
        ok = str(out.get("status", "")).lower() in ("success", "degraded")
        eval_dur = meta.get("eval_duration")
        return _RequestSample(
            ok=ok,
            e2e_ms=e2e_ms,
            prompt_tokens=_as_int(meta.get("prompt_eval_count")),
            completion_tokens=_as_int(meta.get("eval_count")),
            eval_duration_sec=(float(eval_dur) / 1e9) if eval_dur else None,
            degraded=bool(meta.get("degraded")),
        )

    t0 = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, i) for i in range(requests)]
        for f in concurrent.futures.as_completed(futures):
            samples.append(f.result())
    total_sec = max(1e-6, time.perf_counter() - t0)

    ok_samples = [s for s in samples if s.ok]
    lat_all = [s.e2e_ms for s in samples]
    completion_sum = sum((s.completion_tokens or 0) for s in ok_samples)
    prompt_sum = sum((s.prompt_tokens or 0) for s in ok_samples)
    decode_sec = sum((s.eval_duration_sec or 0.0) for s in ok_samples)
    with_tokens = [s for s in ok_samples if s.completion_tokens]
    decode_tps = (completion_sum / decode_sec) if decode_sec > 0 else 0.0
    cost_1k, basis = cost_model.per_1k(samples, requests / total_sec)

    return ScenarioResult(
        concurrency=concurrency,
        requests=requests,
        total_sec=round(total_sec, 3),
        throughput_rps=round(requests / total_sec, 3),
        success=len(ok_samples),
        errors=len(samples) - len(ok_samples),
        latency_p50_ms=_percentile(lat_all, 50),
        latency_p95_ms=_percentile(lat_all, 95),
        latency_p99_ms=_percentile(lat_all, 99),
        latency_mean_ms=int(statistics.mean(lat_all)) if lat_all else 0,
        prompt_tokens=prompt_sum,
        completion_tokens=completion_sum,
        decode_tokens_per_sec=round(decode_tps, 2),
        avg_completion_tokens=round(completion_sum / len(with_tokens), 1) if with_tokens else 0.0,
        est_cost_per_1k_usd=cost_1k,
        cost_basis=basis,
    )


def _as_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def build_document(*, args: argparse.Namespace, rows: list[ScenarioResult]) -> dict[str, Any]:
    return {
        "schema_version": LOADTEST_SCHEMA_VERSION,
        "tool": "load_test",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": _resolve_provider_kind(args),
        "config": {
            "model": args.model,
            "endpoint": None if args.simulate else args.endpoint,
            "requests": int(args.requests),
            "concurrency": [int(c) for c in _parse_levels(args.concurrency)],
            "num_predict": int(args.num_predict),
            "queue_wait_timeout_seconds": float(args.queue_wait_timeout),
            "cost": {
                "input_usd_per_mtok": args.input_usd_per_mtok,
                "output_usd_per_mtok": args.output_usd_per_mtok,
                "gpu_hourly_usd": args.gpu_hourly_usd,
            },
        },
        "metrics_explained": {
            "throughput_rps": "Completed predict() per wall-clock second at this concurrency.",
            "latency_p99_ms": "End-to-end client latency per predict(), 99th percentile.",
            "decode_tokens_per_sec": "Sum(completion tokens) / Sum(provider eval_duration) — raw decode speed.",
            "est_cost_per_1k_usd": "Estimated USD per 1000 inferences (token pricing or amortized GPU-hour).",
        },
        "scenarios": [asdict(r) for r in rows],
    }


def render_markdown(doc: dict[str, Any]) -> str:
    cfg = doc["config"]
    lines = [
        f"# Inference Load Test ({doc['mode']})",
        "",
        f"- generated: `{doc['generated_at']}`",
        f"- model: `{cfg['model']}`  endpoint: `{cfg['endpoint']}`",
        f"- requests/level: {cfg['requests']}  num_predict: {cfg['num_predict']}",
        "",
        "| concurrency | rps | p50 ms | p95 ms | p99 ms | success | errors | decode tok/s | avg out tok | $/1k |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in doc["scenarios"]:
        lines.append(
            f"| {s['concurrency']} | {s['throughput_rps']:.2f} | {s['latency_p50_ms']} | "
            f"{s['latency_p95_ms']} | {s['latency_p99_ms']} | {s['success']} | {s['errors']} | "
            f"{s['decode_tokens_per_sec']:.1f} | {s['avg_completion_tokens']:.1f} | "
            f"{s['est_cost_per_1k_usd']:.4f} |"
        )
    basis = doc["scenarios"][0]["cost_basis"] if doc["scenarios"] else "unpriced"
    lines += ["", f"_Cost basis: {basis}._", ""]
    return "\n".join(lines)


def render_svg(doc: dict[str, Any]) -> str:
    """Hand-rolled dependency-free SVG: throughput bars + latency percentile lines."""
    rows = doc["scenarios"]
    if not rows:
        return "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='40'></svg>"
    W, H = 720, 380
    pad_l, pad_r, pad_t, pad_b = 60, 60, 50, 50
    plot_w = W - pad_l - pad_r
    plot_h = H - pad_t - pad_b
    n = len(rows)
    xs = [pad_l + plot_w * (i + 0.5) / n for i in range(n)]

    max_rps = max((r["throughput_rps"] for r in rows), default=1.0) or 1.0
    max_lat = max((r["latency_p99_ms"] for r in rows), default=1.0) or 1.0

    def y_rps(v: float) -> float:
        return pad_t + plot_h * (1 - v / (max_rps * 1.15))

    def y_lat(v: float) -> float:
        return pad_t + plot_h * (1 - v / (max_lat * 1.15))

    parts: list[str] = [
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{W}' height='{H}' font-family='sans-serif'>",
        f"<rect width='{W}' height='{H}' fill='white'/>",
        f"<text x='{W/2}' y='26' text-anchor='middle' font-size='16' font-weight='bold'>"
        f"Inference Load Test — {doc['mode']} ({rows[0].get('cost_basis','')})</text>",
        f"<line x1='{pad_l}' y1='{pad_t+plot_h}' x2='{pad_l+plot_w}' y2='{pad_t+plot_h}' stroke='#999'/>",
        f"<line x1='{pad_l}' y1='{pad_t}' x2='{pad_l}' y2='{pad_t+plot_h}' stroke='#999'/>",
        f"<text x='16' y='{pad_t+plot_h/2}' text-anchor='middle' font-size='11' fill='#1f77b4' "
        f"transform='rotate(-90 16 {pad_t+plot_h/2})'>throughput (rps)</text>",
    ]
    # Throughput bars (blue).
    bar_w = plot_w / n * 0.5
    for i, r in enumerate(rows):
        bx = xs[i] - bar_w / 2
        by = y_rps(r["throughput_rps"])
        bh = pad_t + plot_h - by
        parts.append(
            f"<rect x='{bx:.1f}' y='{by:.1f}' width='{bar_w:.1f}' height='{bh:.1f}' "
            f"fill='#1f77b4' opacity='0.75'/>"
        )
        parts.append(
            f"<text x='{xs[i]:.1f}' y='{by-4:.1f}' text-anchor='middle' font-size='10' fill='#1f77b4'>"
            f"{r['throughput_rps']:.1f}</text>"
        )
        parts.append(
            f"<text x='{xs[i]:.1f}' y='{pad_t+plot_h+16:.1f}' text-anchor='middle' font-size='11'>"
            f"c={r['concurrency']}</text>"
        )

    # Latency percentile lines (p50/p95/p99) on a shared axis (right-side scale, red tones).
    for key, color in (("latency_p50_ms", "#2ca02c"), ("latency_p95_ms", "#ff7f0e"), ("latency_p99_ms", "#d62728")):
        pts = " ".join(f"{xs[i]:.1f},{y_lat(rows[i][key]):.1f}" for i in range(n))
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{color}' stroke-width='2'/>")
        for i in range(n):
            parts.append(f"<circle cx='{xs[i]:.1f}' cy='{y_lat(rows[i][key]):.1f}' r='3' fill='{color}'/>")

    # Legend.
    legend = [("throughput rps", "#1f77b4"), ("p50 ms", "#2ca02c"), ("p95 ms", "#ff7f0e"), ("p99 ms", "#d62728")]
    lx = pad_l + 8
    for label, color in legend:
        parts.append(f"<rect x='{lx}' y='{pad_t+6}' width='11' height='11' fill='{color}'/>")
        parts.append(f"<text x='{lx+15}' y='{pad_t+16}' font-size='11'>{label}</text>")
        lx += 14 + 8 * len(label)
    parts.append(
        f"<text x='{pad_l+plot_w}' y='{pad_t+plot_h/2}' text-anchor='middle' font-size='11' fill='#d62728' "
        f"transform='rotate(90 {pad_l+plot_w} {pad_t+plot_h/2})'>latency (ms)</text>"
    )
    parts.append("</svg>")
    return "\n".join(parts)


def _parse_levels(raw: str) -> list[int]:
    return [max(1, int(x.strip())) for x in str(raw).split(",") if x.strip()]


def _write(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-provider inference load test.")
    parser.add_argument("--simulate", action="store_true", help="Shorthand for --provider simulate (no GPU).")
    parser.add_argument(
        "--provider",
        default="ollama",
        choices=["ollama", "vllm", "openai", "simulate"],
        help="Inference backend to load test.",
    )
    parser.add_argument(
        "--endpoint",
        default="http://localhost:11434",
        help="Backend base URL (Ollama :11434; vLLM/OpenAI :8000 or .../v1).",
    )
    parser.add_argument("--api-key", default="", help="Bearer token for OpenAI-compatible endpoints (optional).")
    parser.add_argument("--model", default="llava:7b", help="Model tag (real) or label (simulate).")
    parser.add_argument("--image", default="", help="Image path sent on every request (required for real Ollama).")
    parser.add_argument("--prompt", default='Rate this concert photo 0-100 as JSON {"overall": n}.')
    parser.add_argument("--prompt-file", default="", help="Read prompt from a file (overrides --prompt).")
    parser.add_argument("--requests", type=int, default=40, help="Requests per concurrency level.")
    parser.add_argument("--concurrency", default="1,2,4", help="Comma-separated worker/submitter levels.")
    parser.add_argument("--num-predict", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=0)
    parser.add_argument("--queue-wait-timeout", type=float, default=600.0)
    # Cost model (choose token pricing OR gpu hourly; gpu_hourly wins if set).
    parser.add_argument("--input-usd-per-mtok", type=float, default=0.0)
    parser.add_argument("--output-usd-per-mtok", type=float, default=0.0)
    parser.add_argument("--gpu-hourly-usd", type=float, default=0.0, help="Amortize GPU $/hour over throughput.")
    # Simulate knobs.
    parser.add_argument("--sim-latency-ms", type=int, default=300)
    parser.add_argument("--sim-prompt-tokens", type=int, default=620)
    parser.add_argument("--sim-completion-tokens", type=int, default=180)
    parser.add_argument("--sim-error-rate", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=20260619)
    # Output.
    parser.add_argument("--output-json", default="")
    parser.add_argument("--output-md", default="")
    parser.add_argument("--output-svg", default="")
    parser.add_argument("--json", action="store_true", help="Print JSON document to stdout instead of a table.")
    args = parser.parse_args()

    if args.prompt_file:
        args.prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    if _resolve_provider_kind(args) != "simulate" and not args.image:
        raise SystemExit("--image is required for real backend runs (use --simulate for a no-GPU smoke test).")

    levels = _parse_levels(args.concurrency)
    if not levels:
        raise SystemExit("No valid --concurrency values.")

    cost_model = CostModel(
        input_usd_per_mtok=args.input_usd_per_mtok,
        output_usd_per_mtok=args.output_usd_per_mtok,
        gpu_hourly_usd=args.gpu_hourly_usd,
    )

    rows: list[ScenarioResult] = []
    for c in levels:
        provider = _build_provider(args)  # fresh provider per level (stateless)
        rows.append(
            run_scenario(
                concurrency=c,
                requests=args.requests,
                provider=provider,
                model_name=args.model,
                image_path=args.image or "/tmp/loadtest-sim.jpg",
                prompt=args.prompt,
                queue_wait_timeout_seconds=args.queue_wait_timeout,
                timeout=args.timeout,
                cost_model=cost_model,
            )
        )

    doc = build_document(args=args, rows=rows)
    if args.output_json:
        _write(args.output_json, json.dumps(doc, ensure_ascii=False, indent=2))
    if args.output_md:
        _write(args.output_md, render_markdown(doc))
    if args.output_svg:
        _write(args.output_svg, render_svg(doc))

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        return

    print(render_markdown(doc))


if __name__ == "__main__":
    main()
