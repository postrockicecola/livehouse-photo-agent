"use client";

import { useEffect, useState } from "react";
import {
  fetchStudioInfraOverview,
  fetchStudioLifetimeStats,
  type StudioInfraOverview,
  type StudioLifetimeStats,
} from "@/lib/studioApi";
import {
  formatAvgProcessingTime,
  formatRuntimeHours,
  formatStatPercent,
} from "@/lib/studioUi";

function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="rounded-xl border border-stroke bg-panel2 p-4">
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-zinc-100">{value}</div>
      {hint ? <div className="mt-1 text-xs leading-snug text-zinc-500">{hint}</div> : null}
    </div>
  );
}

export function InfraLifetimeMetrics() {
  const [lifetime, setLifetime] = useState<StudioLifetimeStats | null>(null);
  const [infra, setInfra] = useState<StudioInfraOverview | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tick = async () => {
      try {
        const [stats, overview] = await Promise.all([
          fetchStudioLifetimeStats(),
          fetchStudioInfraOverview(),
        ]);
        if (!cancelled) {
          setLifetime(stats);
          setInfra(overview);
        }
      } catch {
        if (!cancelled) {
          setLifetime(null);
          setInfra(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
      timer = setTimeout(tick, 15_000);
    };

    tick();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  const rejectPct =
    lifetime?.auto_reject_rate_pct ?? lifetime?.auto_filter_rate_pct ?? null;

  return (
    <section aria-label="Lifetime pipeline metrics">
      <div className="mb-3">
        <h2 className="text-sm font-medium uppercase tracking-wide text-zinc-400">Lifetime pipeline</h2>
        <p className="mt-1 text-xs text-zinc-500">
          Archive-wide quality and runtime signals (business metrics live on Studio Workbench).
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MetricCard
          label="Auto Reject Rate"
          value={formatStatPercent(rejectPct, loading)}
          hint="Share of ingested photos not exported"
        />
        <MetricCard
          label="Average Processing Time"
          value={formatAvgProcessingTime(lifetime?.avg_processing_sec, loading)}
          hint="Mean succeeded analyze job duration"
        />
        <MetricCard
          label="Runtime Hours"
          value={formatRuntimeHours(lifetime?.total_runtime_hours, lifetime?.total_runtime_sec, loading)}
          hint="Cumulative worker time on analyze jobs"
        />
        <MetricCard
          label="Success Rate"
          value={formatStatPercent(infra?.pipeline_success_rate_pct, loading)}
          hint="Succeeded vs terminal failed jobs (Brain)"
        />
      </div>
    </section>
  );
}
