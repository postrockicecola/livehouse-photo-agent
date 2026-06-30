"use client";

import type { InfraMetricsSnapshot, InfraProviderRow } from "./types";

type Props = {
  providers: InfraProviderRow[];
  activeProvider: string;
  metrics: InfraMetricsSnapshot | null;
  loading?: boolean;
};

type ProviderStats = {
  requests: number;
  failures: number;
  fallbacks: number;
  avgLatency: number | null;
  lastLatency: number | null;
  errorRate: number | null;
  throughput: number | null;
};

function mergeProviderStats(
  p: InfraProviderRow,
  metrics: InfraMetricsSnapshot | null,
): ProviderStats {
  const rt = p.runtime ?? {};
  const proc = (metrics?.providers ?? []).find(
    (x) => (x.provider ?? "").toLowerCase() === p.name.toLowerCase(),
  );
  const db = (metrics?.inference_from_database?.by_provider ?? []).find(
    (x) => x.provider.toLowerCase() === p.name.toLowerCase(),
  );

  const requests = Number(rt.requests ?? proc?.requests ?? db?.terminal_total ?? 0);
  const failures = Number(rt.failures ?? proc?.failures ?? db?.failed_total ?? 0);
  const fallbacks = Number(rt.fallbacks ?? proc?.fallbacks ?? 0);
  const avgLatency =
    rt.avg_latency_ms ?? proc?.avg_latency_ms ?? db?.avg_provider_latency_ms ?? null;
  const lastLatency = rt.last_latency_ms ?? proc?.last_latency_ms ?? null;
  const terminal = requests || Number(db?.terminal_total ?? 0);
  const failCount = failures || Number(db?.failed_total ?? 0);
  const errorRate = terminal > 0 ? (failCount / terminal) * 100 : null;
  const throughput = metrics?.inference_queue?.throughput_img_per_sec_30s ?? null;

  return { requests, failures, fallbacks, avgLatency, lastLatency, errorRate, throughput };
}

export function ProviderPanel({ providers, activeProvider, metrics, loading }: Props) {
  return (
    <section className="rounded-2xl border border-stroke/80 bg-[#08090c] p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-xs uppercase tracking-[0.22em] text-zinc-500">Providers</h2>
          <p className="mt-1 text-sm text-zinc-400">inference routing · latency · error surface</p>
        </div>
        <div className="font-mono text-[10px] text-zinc-600">
          active route · <span className="text-violet-300/90">{activeProvider || "—"}</span>
        </div>
      </div>

      {loading && !providers.length ? (
        <div className="py-6 font-mono text-xs text-zinc-600">loading provider nodes…</div>
      ) : !providers.length ? (
        <div className="py-6 font-mono text-xs text-zinc-600">no providers configured</div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {providers.map((p) => {
            const stats = mergeProviderStats(p, metrics);
            const isActive = p.name.toLowerCase() === activeProvider.toLowerCase();
            return (
              <div
                key={p.name}
                className={`relative overflow-hidden rounded-xl border p-3 ${
                  isActive
                    ? "border-violet-500/35 bg-violet-950/15 shadow-[0_0_28px_rgba(139,92,246,0.06)]"
                    : "border-stroke/70 bg-[#0a0b0f]"
                }`}
              >
                {isActive ? (
                  <div className="absolute right-2 top-2 font-mono text-[9px] uppercase tracking-wider text-violet-300/70">
                    routed
                  </div>
                ) : null}
                <div className="font-mono text-sm text-zinc-200">{p.display_name ?? p.name}</div>
                <div className="mt-0.5 truncate font-mono text-[10px] text-zinc-600">
                  {p.model_name ?? "—"}
                  {p.fallback_model_name ? ` → ${p.fallback_model_name}` : ""}
                </div>

                <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 font-mono text-[10px]">
                  <div>
                    <div className="text-zinc-600">latency</div>
                    <div className="tabular-nums text-zinc-300">
                      {stats.avgLatency != null ? `${stats.avgLatency}ms` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-zinc-600">error rate</div>
                    <div
                      className={`tabular-nums ${
                        (stats.errorRate ?? 0) > 5 ? "text-red-300/90" : "text-zinc-300"
                      }`}
                    >
                      {stats.errorRate != null ? `${stats.errorRate.toFixed(1)}%` : "—"}
                    </div>
                  </div>
                  <div>
                    <div className="text-zinc-600">requests</div>
                    <div className="tabular-nums text-zinc-300">{stats.requests}</div>
                  </div>
                  <div>
                    <div className="text-zinc-600">throughput</div>
                    <div className="tabular-nums text-zinc-300">
                      {stats.throughput != null ? `${stats.throughput}/s` : "—"}
                    </div>
                  </div>
                </div>

                <div className="mt-2 flex gap-2 font-mono text-[9px] text-zinc-600">
                  <span className={p.enabled ? "text-emerald-400/70" : "text-zinc-600"}>
                    {p.enabled ? "enabled" : "disabled"}
                  </span>
                  {stats.failures > 0 ? <span className="text-red-300/70">fail {stats.failures}</span> : null}
                  {stats.fallbacks > 0 ? <span className="text-amber-300/70">fb {stats.fallbacks}</span> : null}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
