"use client";

import Link from "next/link";
import type { StudioInfraOverview } from "@/lib/studioApi";

type Props = {
  data: StudioInfraOverview | null;
  loading?: boolean;
};

function formatJobs(n: number, loading: boolean): string {
  if (loading) return "…";
  return n.toLocaleString("en-US");
}

function formatLatencyMs(ms: number | null | undefined, loading: boolean): string {
  if (loading) return "…";
  if (ms == null || ms <= 0) return "—";
  if (ms >= 60_000) {
    const totalSec = Math.round(ms / 1000);
    const m = Math.floor(totalSec / 60);
    const s = totalSec % 60;
    return `${m}m${s.toString().padStart(2, "0")}s`;
  }
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

function formatPercent(pct: number | null | undefined, loading: boolean): string {
  if (loading) return "…";
  if (pct == null || pct < 0) return "—";
  return `${pct}%`;
}

function healthLabel(status: string | undefined, loading: boolean): string {
  if (loading) return "…";
  const s = String(status ?? "").toLowerCase();
  if (s === "online") return "Online";
  if (s === "offline") return "Offline";
  return "Unknown";
}

function MetricCell({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.02] px-3 py-3.5 sm:px-4 sm:py-4">
      <p className="text-lg font-light tabular-nums tracking-tight text-white/90 sm:text-xl">{value}</p>
      <p className="mt-1.5 font-mono text-[9px] uppercase leading-snug tracking-[0.14em] text-white/34 sm:text-[10px]">
        {label}
      </p>
    </div>
  );
}

export function StudioInfraOverview({ data, loading }: Props) {
  const workers =
    data && data.workers_total > 0
      ? `${data.workers_online} / ${data.workers_total}`
      : formatJobs(data?.workers_online ?? 0, Boolean(loading));

  return (
    <section className="w-full" aria-label="AI infrastructure overview">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-white/34">AI Infra Overview</p>
          <p className="mt-1.5 font-mono text-[10px] leading-relaxed text-white/30 sm:text-[11px]">
            FastAPI · Celery · Redis · Multi-stage AI Pipeline
          </p>
        </div>
        <Link
          href="/infra"
          className="font-mono text-[10px] uppercase tracking-[0.12em] text-white/38 transition-colors hover:text-white/58 sm:text-[11px]"
        >
          Jobs console →
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 sm:gap-3 lg:grid-cols-4 xl:grid-cols-7">
        <MetricCell value={workers} label="Workers Online" />
        <MetricCell value={formatJobs(data?.queue_depth ?? 0, Boolean(loading))} label="Queue Depth" />
        <MetricCell value={formatJobs(data?.jobs_processed ?? 0, Boolean(loading))} label="Jobs Processed" />
        <MetricCell
          value={formatLatencyMs(data?.average_latency_ms, Boolean(loading))}
          label="Average Latency"
        />
        <MetricCell
          value={formatPercent(data?.pipeline_success_rate_pct, Boolean(loading))}
          label="Pipeline Success Rate"
        />
        <MetricCell
          value={healthLabel(data?.redis_status, Boolean(loading))}
          label="Redis Status"
        />
        <MetricCell
          value={healthLabel(data?.database_status, Boolean(loading))}
          label="Database Status"
        />
      </div>
    </section>
  );
}
