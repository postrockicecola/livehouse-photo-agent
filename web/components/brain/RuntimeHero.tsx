"use client";

import { useEffect, useRef, useState } from "react";
import {
  computeHealth,
  computeInflight,
  computeP95Latency,
  computeQueueBacklog,
  computeSuccessRate,
  fmtLatency,
  fmtPct,
} from "./utils";
import type { InfraMetricsSnapshot, RuntimeHealth } from "./types";

type Props = {
  metrics: InfraMetricsSnapshot | null;
  loading?: boolean;
  tick?: number;
};

const HEALTH_STYLES: Record<RuntimeHealth, { dot: string; text: string; glow: string }> = {
  HEALTHY: {
    dot: "bg-emerald-400 shadow-[0_0_12px_rgba(52,211,153,0.8)]",
    text: "text-emerald-300",
    glow: "from-emerald-500/10 via-transparent to-transparent",
  },
  DEGRADED: {
    dot: "bg-amber-400 shadow-[0_0_12px_rgba(251,191,36,0.7)]",
    text: "text-amber-300",
    glow: "from-amber-500/10 via-transparent to-transparent",
  },
  CRITICAL: {
    dot: "bg-red-400 shadow-[0_0_12px_rgba(248,113,113,0.7)]",
    text: "text-red-300",
    glow: "from-red-500/12 via-transparent to-transparent",
  },
  UNKNOWN: {
    dot: "bg-zinc-500",
    text: "text-zinc-400",
    glow: "from-zinc-500/5 via-transparent to-transparent",
  },
};

function AnimatedValue({ value, format }: { value: number | null; format: (n: number) => string }) {
  const [display, setDisplay] = useState<number | null>(value);
  const prev = useRef(value);

  useEffect(() => {
    if (value == null) {
      setDisplay(null);
      prev.current = value;
      return;
    }
    const from = prev.current ?? value;
    prev.current = value;
    if (from === value) {
      setDisplay(value);
      return;
    }
    const start = performance.now();
    const dur = 420;
    let raf = 0;
    const step = (t: number) => {
      const p = Math.min(1, (t - start) / dur);
      const eased = 1 - (1 - p) ** 3;
      setDisplay(from + (value - from) * eased);
      if (p < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [value]);

  if (display == null) return <span className="text-zinc-600">—</span>;
  return <span>{format(display)}</span>;
}

function Metric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-[5.5rem]">
      <div className="text-[10px] uppercase tracking-[0.18em] text-zinc-600">{label}</div>
      <div className="mt-0.5 font-mono text-lg tabular-nums text-zinc-100 sm:text-xl">{children}</div>
    </div>
  );
}

export function RuntimeHero({ metrics, loading, tick = 0 }: Props) {
  const health = computeHealth(metrics);
  const style = HEALTH_STYLES[health];
  const workersAlive = Number(metrics?.workers?.fresh_within_120s ?? 0);
  const backlog = computeQueueBacklog(metrics);
  const inflight = computeInflight(metrics);
  const p95 = computeP95Latency(metrics);
  const success = computeSuccessRate(metrics);

  return (
    <section className="runtime-hero relative overflow-hidden rounded-2xl border border-stroke/80 bg-[#08090c]">
      <div className={`pointer-events-none absolute inset-0 bg-gradient-to-br ${style.glow}`} />
      <div className="runtime-grid pointer-events-none absolute inset-0 opacity-[0.35]" />

      <div className="relative px-4 py-5 sm:px-6 sm:py-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.28em] text-zinc-600">
              <span className="runtime-pulse-dot h-1.5 w-1.5 rounded-full bg-sky-400/80" />
              AI Runtime
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <div className={`flex items-center gap-2.5 text-3xl font-semibold tracking-tight sm:text-4xl ${style.text}`}>
                <span className={`runtime-pulse-dot h-3 w-3 rounded-full ${style.dot}`} />
                {loading && !metrics ? "…" : health}
              </div>
            </div>
            <div className="mt-2 font-mono text-[11px] text-zinc-600">
              heartbeat <span className="text-zinc-500">#{tick}</span>
              <span className="mx-2 text-zinc-800">|</span>
              orchestration control plane
            </div>
          </div>

          <div className="flex flex-wrap gap-x-6 gap-y-3 sm:gap-x-8">
            <Metric label="queue backlog">
              <AnimatedValue value={loading && !metrics ? null : backlog} format={(n) => String(Math.round(n))} />
            </Metric>
            <Metric label="workers alive">
              <AnimatedValue value={loading && !metrics ? null : workersAlive} format={(n) => String(Math.round(n))} />
            </Metric>
            <Metric label="inflight jobs">
              <AnimatedValue value={loading && !metrics ? null : inflight} format={(n) => String(Math.round(n))} />
            </Metric>
            <Metric label="p95 latency">
              <AnimatedValue value={loading && !metrics ? null : p95} format={(n) => fmtLatency(n)} />
            </Metric>
            <Metric label="success rate">
              {success == null ? (
                <span className="text-zinc-600">—</span>
              ) : (
                <AnimatedValue value={success} format={(n) => fmtPct(n)} />
              )}
            </Metric>
          </div>
        </div>
      </div>
    </section>
  );
}
