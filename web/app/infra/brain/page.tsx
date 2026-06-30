"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { EventStream } from "@/components/brain/EventStream";
import { FlowGraph } from "@/components/brain/FlowGraph";
import { LedgerSection } from "@/components/brain/LedgerSection";
import { ProviderPanel } from "@/components/brain/ProviderPanel";
import { RuntimeHero } from "@/components/brain/RuntimeHero";
import type {
  BrainDashboardData,
  InfraMetricsSnapshot,
  InfraProviderRow,
  InfraWorkerRow,
  RuntimeStreamData,
} from "@/components/brain/types";
import { WorkerPanel } from "@/components/brain/WorkerPanel";
import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();
const POLL_MS = 4000;

export default function InfraBrainPage() {
  const [metrics, setMetrics] = useState<InfraMetricsSnapshot | null>(null);
  const [stream, setStream] = useState<RuntimeStreamData | null>(null);
  const [ledger, setLedger] = useState<BrainDashboardData | null>(null);
  const [workers, setWorkers] = useState<InfraWorkerRow[]>([]);
  const [providers, setProviders] = useState<InfraProviderRow[]>([]);
  const [activeProvider, setActiveProvider] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    const tickFetch = async () => {
      try {
        const [metricsRes, streamRes, ledgerRes, workersRes, providersRes] = await Promise.all([
          fetch(`${API_BASE}/api/infra/metrics`, { cache: "no-store" }),
          fetch(`${API_BASE}/api/infra/runtime-stream?events_limit=80`, { cache: "no-store" }),
          fetch(`${API_BASE}/api/infra/brain?sessions_limit=15&photos_limit=30`, { cache: "no-store" }),
          fetch(`${API_BASE}/api/infra/workers`, { cache: "no-store" }),
          fetch(`${API_BASE}/api/infra/providers`, { cache: "no-store" }),
        ]);

        if (!metricsRes.ok || !streamRes.ok || !ledgerRes.ok || !workersRes.ok || !providersRes.ok) {
          throw new Error("runtime control plane api failed");
        }

        const metricsData: InfraMetricsSnapshot = await metricsRes.json();
        const streamData: RuntimeStreamData = await streamRes.json();
        const ledgerData: BrainDashboardData = await ledgerRes.json();
        const workersData: { items?: InfraWorkerRow[] } = await workersRes.json();
        const providersData: { active_provider?: string; providers?: InfraProviderRow[] } =
          await providersRes.json();

        if (!cancelled) {
          setMetrics(metricsData);
          setStream(streamData);
          setLedger(ledgerData);
          setWorkers(workersData.items ?? []);
          setProviders(providersData.providers ?? []);
          setActiveProvider(providersData.active_provider ?? "");
          setError(null);
          setTick((t) => t + 1);
        }
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "failed to load runtime plane");
      } finally {
        if (!cancelled) setLoading(false);
      }
      timer = setTimeout(tickFetch, POLL_MS);
    };

    tickFetch();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  return (
    <main className="runtime-shell studio-grain relative min-h-screen px-3 py-4 sm:px-5 sm:py-5">
      <header className="relative z-10 mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.28em] text-zinc-600">
            <span className="runtime-pulse-dot h-1 w-1 rounded-full bg-violet-400/60" />
            Brain · nervous system
          </div>
          <h1 className="mt-1 text-xl font-semibold tracking-tight text-zinc-100 sm:text-2xl">
            Inference Orchestration
          </h1>
          <p className="mt-1 max-w-xl font-mono text-[11px] text-zinc-600">
            runtime-first control plane · queue · workers · stage flow · event stream
          </p>
        </div>
        <nav className="flex flex-wrap gap-2">
          <Link
            href="/infra"
            className="rounded-lg border border-stroke/80 px-3 py-1.5 font-mono text-[11px] text-zinc-400 transition hover:border-stroke hover:bg-zinc-900 hover:text-zinc-200"
          >
            Jobs console
          </Link>
          <Link
            href="/studio"
            className="rounded-lg border border-stroke/80 px-3 py-1.5 font-mono text-[11px] text-zinc-400 transition hover:border-stroke hover:bg-zinc-900 hover:text-zinc-200"
          >
            Studio
          </Link>
          <Link
            href="/gallery"
            className="rounded-lg border border-stroke/80 px-3 py-1.5 font-mono text-[11px] text-zinc-400 transition hover:border-stroke hover:bg-zinc-900 hover:text-zinc-200"
          >
            Gallery
          </Link>
        </nav>
      </header>

      {error ? (
        <div className="relative z-10 mb-4 rounded-lg border border-red-500/30 bg-red-950/20 px-3 py-2 font-mono text-xs text-red-300">
          {error}
        </div>
      ) : null}

      <div className="relative z-10 space-y-4">
        <RuntimeHero metrics={metrics} loading={loading} tick={tick} />
        <FlowGraph
          stages={stream?.stages ?? []}
          retries={stream?.retries_recent ?? []}
          loading={loading}
        />
        <EventStream events={stream?.events ?? []} loading={loading} />
        <div className="grid gap-4 xl:grid-cols-2">
          <WorkerPanel
            workers={workers}
            admission={metrics?.workers?.pipeline_admission ?? null}
            loading={loading}
          />
          <ProviderPanel
            providers={providers}
            activeProvider={activeProvider}
            metrics={metrics}
            loading={loading}
          />
        </div>
        <LedgerSection data={ledger} loading={loading} />
      </div>
    </main>
  );
}
