#!/usr/bin/env python3
"""Minimal benchmark for inference queue throughput/reliability storytelling."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from inference.client import InferenceClient
from inference.providers.base import InferenceProvider
from inference.router import InferenceRouter
from inference.types import InferenceRequest, InferenceResponse


BENCHMARK_SCHEMA_VERSION = "1"


@dataclass
class ScenarioResult:
    workers: int
    requests: int
    concurrency: int
    total_sec: float
    throughput_rps: float
    success: int
    errors: int
    # degraded metadata: queue-timeout→fallback and/or router fallback path
    fallback_count: int
    # fallback_count / requests (0..1)
    fallback_ratio: float
    queue_wait_p50_ms: int
    queue_wait_p95_ms: int
    latency_p50_ms: int
    latency_p95_ms: int


class SimulatedProvider(InferenceProvider):
    PROVIDER_ID = "bench_simulated"

    def __init__(
        self,
        *,
        name: str,
        min_latency_ms: int,
        max_latency_ms: int,
        error_rate: float = 0.0,
    ) -> None:
        self.name = name
        self.min_latency_ms = max(1, int(min_latency_ms))
        self.max_latency_ms = max(self.min_latency_ms, int(max_latency_ms))
        self.error_rate = max(0.0, min(1.0, float(error_rate)))

    def generate(self, request: InferenceRequest, *, model_name: str) -> InferenceResponse:
        latency_ms = random.randint(self.min_latency_ms, self.max_latency_ms)
        time.sleep(latency_ms / 1000.0)
        if random.random() < self.error_rate:
            return InferenceResponse(
                status="error",
                error=f"{self.name} simulated error",
                model=model_name,
                metadata={"provider": self.name, "provider_latency_ms": latency_ms},
            )
        return InferenceResponse(
            status="success",
            text='{"score": 7.5}',
            model=model_name,
            metadata={"provider": self.name, "provider_latency_ms": latency_ms},
        )


def _percentile(values: list[float], p: float) -> int:
    if not values:
        return 0
    if len(values) == 1:
        return int(values[0])
    q = statistics.quantiles(values, n=100, method="inclusive")
    idx = max(0, min(99, int(p) - 1))
    return int(q[idx])


def run_scenario(
    *,
    workers: int,
    requests: int,
    concurrency: int,
    queue_wait_timeout_seconds: float,
    primary_latency_ms: int,
    fallback_latency_ms: int,
    primary_error_rate: float,
) -> ScenarioResult:
    primary = SimulatedProvider(
        name="primary",
        min_latency_ms=max(1, int(primary_latency_ms * 0.7)),
        max_latency_ms=max(2, int(primary_latency_ms * 1.3)),
        error_rate=primary_error_rate,
    )
    fallback = SimulatedProvider(
        name="fallback",
        min_latency_ms=max(1, int(fallback_latency_ms * 0.8)),
        max_latency_ms=max(2, int(fallback_latency_ms * 1.2)),
        error_rate=0.0,
    )
    router = InferenceRouter(
        primary_provider=primary,
        primary_model_name="primary-model",
        fallback_provider=fallback,
        fallback_model_name="fallback-model",
    )
    client = InferenceClient(
        router=router,
        queue_wait_timeout_seconds=queue_wait_timeout_seconds,
        num_workers=workers,
        timeout=max(5, int(primary_latency_ms / 1000) + 1),
        max_retries=0,
    )

    waits_ms: list[float] = []
    lat_ms: list[float] = []
    fallback_count = 0
    success = 0
    errors = 0

    t0 = time.perf_counter()

    def one_request(i: int) -> dict[str, Any]:
        start = time.perf_counter()
        out = client.predict(
            image_path=f"/tmp/bench-{i}.jpg",
            prompt='{"task":"score"}',
            priority=0,
            trace_id=f"bench-{workers}-{i}",
        )
        out["client_elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        return out

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one_request, i) for i in range(requests)]
        for f in concurrent.futures.as_completed(futures):
            result = f.result()
            meta = dict(result.get("metadata") or {})
            waits_ms.append(float(meta.get("queue_wait_sec", 0.0)) * 1000.0)
            lat_ms.append(float(result.get("client_elapsed_ms", 0)))
            if bool(meta.get("degraded")):
                fallback_count += 1
            if str(result.get("status")).lower() == "success":
                success += 1
            else:
                errors += 1

    total_sec = max(1e-6, time.perf_counter() - t0)
    fb_ratio = float(fallback_count) / float(max(1, requests))
    return ScenarioResult(
        workers=workers,
        requests=requests,
        concurrency=concurrency,
        total_sec=total_sec,
        throughput_rps=requests / total_sec,
        success=success,
        errors=errors,
        fallback_count=fallback_count,
        fallback_ratio=fb_ratio,
        queue_wait_p50_ms=_percentile(waits_ms, 50),
        queue_wait_p95_ms=_percentile(waits_ms, 95),
        latency_p50_ms=_percentile(lat_ms, 50),
        latency_p95_ms=_percentile(lat_ms, 95),
    )


def build_benchmark_document(*, args: argparse.Namespace, rows: list[ScenarioResult]) -> dict[str, Any]:
    """Machine-readable run (resume / dashboards); stable keys."""
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "tool": "benchmark_inference",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "requests": int(args.requests),
            "concurrency": int(args.concurrency),
            "workers": [max(1, int(x.strip())) for x in args.workers.split(",") if x.strip()],
            "queue_wait_timeout_seconds": float(args.queue_wait_timeout),
            "primary_latency_ms": int(args.primary_latency_ms),
            "fallback_latency_ms": int(args.fallback_latency_ms),
            "primary_error_rate": float(args.primary_error_rate),
        },
        "metrics_explained": {
            "throughput_rps": "Completed submits per wall-clock second (includes queueing).",
            "queue_wait_p50_ms": "Inference-layer queue wait before a worker starts the attempt (p50).",
            "latency_p50_ms": "End-to-end client time per predict() (p50).",
            "fallback_ratio": "Fraction of requests with degraded metadata (timeout→fallback or router fallback).",
        },
        "scenarios": [asdict(r) for r in rows],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Minimal inference queue benchmark.")
    parser.add_argument("--requests", type=int, default=120, help="Total requests per scenario.")
    parser.add_argument("--concurrency", type=int, default=24, help="Client-side concurrent submitters.")
    parser.add_argument(
        "--workers",
        type=str,
        default="1,4",
        help="Comma-separated inference queue workers, e.g. 1,2,4",
    )
    parser.add_argument("--queue-wait-timeout", type=float, default=0.8, help="Force fallback when queue wait exceeds this.")
    parser.add_argument("--primary-latency-ms", type=int, default=220, help="Primary provider simulated latency.")
    parser.add_argument("--fallback-latency-ms", type=int, default=80, help="Fallback provider simulated latency.")
    parser.add_argument("--primary-error-rate", type=float, default=0.08, help="Primary provider error rate [0,1].")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit one JSON document (stdout) with schema_version, config, and per-worker scenarios.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default="",
        help="If set, also write the same JSON document to this path (parent dirs created).",
    )
    args = parser.parse_args()

    worker_list = [max(1, int(x.strip())) for x in args.workers.split(",") if x.strip()]
    if not worker_list:
        raise SystemExit("No valid --workers values")

    rows: list[ScenarioResult] = []
    for w in worker_list:
        rows.append(
            run_scenario(
                workers=w,
                requests=args.requests,
                concurrency=args.concurrency,
                queue_wait_timeout_seconds=args.queue_wait_timeout,
                primary_latency_ms=args.primary_latency_ms,
                fallback_latency_ms=args.fallback_latency_ms,
                primary_error_rate=args.primary_error_rate,
            )
        )

    doc = build_benchmark_document(args=args, rows=rows)
    out_path = (args.output_json or "").strip()
    if out_path:
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        raise SystemExit(0)

    print("# Inference Benchmark")
    print(
        f"requests={args.requests} concurrency={args.concurrency} queue_wait_timeout={args.queue_wait_timeout}s "
        f"primary_latency={args.primary_latency_ms}ms fallback_latency={args.fallback_latency_ms}ms "
        f"primary_error_rate={args.primary_error_rate}"
    )
    print("")
    print(
        "workers | throughput(rps) | success | errors | fallback | fb_ratio | "
        "qwait_p50/p95(ms) | latency_p50/p95(ms)"
    )

    for result in rows:
        print(
            f"{result.workers:>7} | "
            f"{result.throughput_rps:>15.2f} | "
            f"{result.success:>7} | "
            f"{result.errors:>6} | "
            f"{result.fallback_count:>8} | "
            f"{result.fallback_ratio:>8.3f} | "
            f"{result.queue_wait_p50_ms:>5}/{result.queue_wait_p95_ms:<5} | "
            f"{result.latency_p50_ms:>5}/{result.latency_p95_ms:<5}"
        )


if __name__ == "__main__":
    main()
