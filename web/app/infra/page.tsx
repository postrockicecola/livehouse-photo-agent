"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { InfraWorkerRow } from "@/components/WorkersPanel";
import { JobsTable } from "@/components/JobsTable";
import { AgentCurationPanel } from "@/components/infra/AgentCurationPanel";
import { CapacityAdmissionPanel } from "@/components/infra/CapacityAdmissionPanel";
import { FailureCenter } from "@/components/infra/FailureCenter";
import { GoldenSignals } from "@/components/infra/GoldenSignals";
import { LivePipelineTimeline } from "@/components/infra/LivePipelineTimeline";
import { PipelineTopology } from "@/components/infra/PipelineTopology";
import { ProviderRuntimePanel, type ProviderRow } from "@/components/infra/ProviderRuntimePanel";
import { SLOStrip } from "@/components/infra/SLOStrip";
import { SystemStatusBar } from "@/components/infra/SystemStatusBar";
import { WorkerPoolPanel } from "@/components/infra/WorkerPoolPanel";
import { CostAttributionPanel, type CostData } from "@/components/infra/CostAttributionPanel";
import { ServingThroughputPanel, type InferenceQueueSnapshot } from "@/components/infra/ServingThroughputPanel";
import { RLHFVotePanel } from "@/components/infra/RLHFVotePanel";
import { PromptExperimentPanel } from "@/components/infra/PromptExperimentPanel";
import { InfraExperimentsSection } from "@/components/infra/InfraExperimentsSection";
import type { DeadLetterJobRow } from "@/components/DeadLetterPanel";
import {
  aggregateFailureBuckets,
  deriveSystemHealth,
  deriveThroughputPerMin,
  errorRatePct,
  pipelineUtilizationPct,
  type InfraHistoryPoint,
  type InfraStageFlowItem,
} from "@/lib/infraControlPlane";
import { getApiBase } from "@/lib/apiBase";
import { ShowcaseBanner } from "@/components/ShowcaseBanner";

const API_BASE = getApiBase();

type QueueBacklog = {
  active?: number;
  reserved?: number;
  scheduled?: number;
  redis_list_len?: number | null;
  celery_unavailable?: boolean;
  redis_error?: string | null;
  workers?: number;
};

type PipelineAdmission = {
  headroom?: number;
  total_capacity?: number;
  total_inflight?: number;
  online_workers?: number;
  total_worker_rows?: number;
};

type InfraMetrics = {
  jobs?: {
    total?: number;
    by_status?: Record<string, number>;
  };
  queue_backlog?: QueueBacklog;
  workers?: {
    total?: number;
    fresh_within_120s?: number;
    heartbeat_fresh_window_sec?: number;
    by_status?: Record<string, number>;
    executor_pools?: Record<string, unknown>;
    pipeline_admission?: PipelineAdmission;
  };
  model_runs?: {
    by_error_type?: Record<string, number>;
  };
  inference_queue?: InferenceQueueSnapshot;
  latency?: {
    sample_count?: number;
    total_latency_ms?: { p50?: number | null; p95?: number | null; p99?: number | null; max?: number | null };
    queue_wait_ms?: { p50?: number | null; p95?: number | null };
  };
  slo?: InfraSloWindow;
  providers?: Array<{ fallbacks?: number; provider?: string; [key: string]: unknown }>;
};

export type InfraSloWindow = {
  window_sec?: number;
  target_pct?: number;
  completed?: number;
  succeeded?: number;
  failed?: number;
  success_rate_pct?: number | null;
  error_budget_remaining_pct?: number | null;
};

type InfraHistoryApiPoint = {
  ts: number;
  queued?: number;
  running?: number;
  failed_total?: number;
  succeeded_cumulative?: number;
  util_pct?: number | null;
  p50_ms?: number | null;
  p95_ms?: number | null;
  throughput_per_min?: number | null;
};

type InfraRuntimeStream = {
  stages?: InfraStageFlowItem[];
  retries_recent?: unknown[];
  events?: Array<{
    id: number;
    job_id: number;
    from_status?: string | null;
    to_status?: string | null;
    created_at: number;
    message?: string | null;
    stage_name?: string | null;
    worker_id?: number | null;
    worker_name?: string | null;
    trace_id?: string | null;
  }>;
};

const PIPELINE_ACTIVE = new Set(["CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"]);
const MAX_HISTORY = 120; // ~6 min at the 3s poll interval

async function readJson<T>(res: PromiseSettledResult<Response>): Promise<{ ok: boolean; data: T | null }> {
  if (res.status !== "fulfilled" || !res.value.ok) return { ok: false, data: null };
  try {
    return { ok: true, data: (await res.value.json()) as T };
  } catch {
    return { ok: false, data: null };
  }
}

export default function InfraPage() {
  const [metrics, setMetrics] = useState<InfraMetrics | null>(null);
  const [runtimeStream, setRuntimeStream] = useState<InfraRuntimeStream | null>(null);
  const [deadLetter, setDeadLetter] = useState<Array<Record<string, unknown>>>([]);
  const [workers, setWorkers] = useState<Array<Record<string, unknown>>>([]);
  const [workersBroker, setWorkersBroker] = useState<{
    celery_unavailable?: boolean;
    worker_count?: number;
  }>();
  const [unmatchedBrokerWorkers, setUnmatchedBrokerWorkers] = useState<Array<Record<string, unknown>>>([]);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [activeProvider, setActiveProvider] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [degradedSources, setDegradedSources] = useState<string[]>([]);
  const [lastUpdatedMs, setLastUpdatedMs] = useState<number | null>(null);
  const [history, setHistory] = useState<InfraHistoryPoint[]>([]);
  const lastPointRef = useRef<InfraHistoryPoint | undefined>(undefined);
  const [costData, setCostData] = useState<CostData | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: NodeJS.Timeout;

    const tick = async () => {
      const settled = await Promise.allSettled([
        fetch(`${API_BASE}/api/infra/metrics`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/infra/dead-letter?limit=15&offset=0`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/infra/workers`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/infra/providers`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/infra/runtime-stream?events_limit=40`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/infra/cost`, { cache: "no-store" }),
      ]);

      const [metricsR, dlR, workersR, providersR, streamR, costR] = settled;
      const metricsParsed = await readJson<InfraMetrics>(metricsR);
      const dlParsed = await readJson<{ items?: Array<Record<string, unknown>> }>(dlR);
      const workersParsed = await readJson<{
        items?: Array<Record<string, unknown>>;
        broker?: { celery_unavailable?: boolean; worker_count?: number };
        unmatched_broker_workers?: Array<Record<string, unknown>>;
      }>(workersR);
      const providersParsed = await readJson<{ providers?: ProviderRow[]; active_provider?: string }>(providersR);
      const streamParsed = await readJson<InfraRuntimeStream>(streamR);
      const costParsed = await readJson<CostData>(costR);

      if (cancelled) return;

      const degraded: string[] = [];
      if (!metricsParsed.ok) degraded.push("metrics");
      if (!dlParsed.ok) degraded.push("dead-letter");
      if (!workersParsed.ok) degraded.push("workers");
      if (!providersParsed.ok) degraded.push("providers");
      if (!streamParsed.ok) degraded.push("runtime-stream");
      if (!costParsed.ok) degraded.push("cost");

      // Per-source update: keep last-known-good data for any feed that failed.
      if (metricsParsed.ok && metricsParsed.data) setMetrics(metricsParsed.data);
      if (streamParsed.ok && streamParsed.data) setRuntimeStream(streamParsed.data);
      if (dlParsed.ok && dlParsed.data) setDeadLetter(dlParsed.data.items ?? []);
      if (workersParsed.ok && workersParsed.data) {
        setWorkers(workersParsed.data.items ?? []);
        setWorkersBroker(workersParsed.data.broker ?? undefined);
        setUnmatchedBrokerWorkers(workersParsed.data.unmatched_broker_workers ?? []);
      }
      if (providersParsed.ok && providersParsed.data) {
        setProviders(providersParsed.data.providers ?? []);
        setActiveProvider(providersParsed.data.active_provider ?? "");
      }
      if (costParsed.ok && costParsed.data) setCostData(costParsed.data);

      setDegradedSources(degraded);
      setLoading(false);

      // Accumulate a real client-observed sample whenever metrics succeed.
      if (metricsParsed.ok && metricsParsed.data) {
        const now = Date.now();
        setLastUpdatedMs(now);
        const md = metricsParsed.data;
        const bs = md.jobs?.by_status ?? {};
        const adm = md.workers?.pipeline_admission;
        const inflight = adm?.total_inflight ?? 0;
        const cap = adm?.total_capacity ?? 0;
        const util = pipelineUtilizationPct(inflight, cap > 0 ? cap : undefined);
        const succeeded = Number(bs.SUCCEEDED ?? 0);
        const failedTotal =
          Number(bs.FAILED_RETRYABLE ?? 0) + Number(bs.FAILED_PERMANENT ?? 0) + Number(bs.DEAD_LETTERED ?? 0);
        let running = 0;
        for (const [st, c] of Object.entries(bs)) if (PIPELINE_ACTIVE.has(st)) running += Number(c ?? 0);
        const p95 = md.latency?.total_latency_ms?.p95;
        const pipelineAvgMs = p95 != null ? Number(p95) : null;
        const point: InfraHistoryPoint = {
          t: now,
          queued: Number(bs.QUEUED ?? 0),
          running,
          failedTotal,
          succeededCumulative: succeeded,
          utilPct: util,
          pipelineAvgMs,
          throughputPerMin: deriveThroughputPerMin(lastPointRef.current, succeeded, now),
        };
        lastPointRef.current = point;
        setHistory((prev) => [...prev, point].slice(-MAX_HISTORY));
      }

      timer = setTimeout(tick, 3000);
    };

    tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  // Seed sparkline history from server-persisted samples so trends survive reloads.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/api/infra/metrics/history?window_sec=3600&limit=${MAX_HISTORY}`, {
          cache: "no-store",
        });
        if (!r.ok) return;
        const j = (await r.json()) as { points?: InfraHistoryApiPoint[] };
        const seeded: InfraHistoryPoint[] = (j.points ?? []).map((p) => ({
          t: p.ts * 1000,
          queued: Number(p.queued ?? 0),
          running: Number(p.running ?? 0),
          failedTotal: Number(p.failed_total ?? 0),
          succeededCumulative: Number(p.succeeded_cumulative ?? 0),
          utilPct: p.util_pct ?? null,
          pipelineAvgMs: p.p95_ms ?? null,
          throughputPerMin: p.throughput_per_min ?? null,
        }));
        if (cancelled || !seeded.length) return;
        setHistory((prev) => {
          if (!prev.length) return seeded.slice(-MAX_HISTORY);
          const firstT = prev[0].t;
          const older = seeded.filter((p) => p.t < firstT);
          return [...older, ...prev].slice(-MAX_HISTORY);
        });
      } catch {
        /* best-effort seeding */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const jobsByStatus = metrics?.jobs?.by_status ?? {};

  const jobsQueued = Number(jobsByStatus.QUEUED ?? 0);
  const jobsActivePipeline = useMemo(() => {
    let n = 0;
    for (const [st, c] of Object.entries(jobsByStatus)) {
      if (PIPELINE_ACTIVE.has(st)) n += Number(c ?? 0);
    }
    return n;
  }, [jobsByStatus]);

  const failedJobs =
    Number(jobsByStatus.FAILED_RETRYABLE ?? 0) +
    Number(jobsByStatus.FAILED_PERMANENT ?? 0) +
    Number(jobsByStatus.DEAD_LETTERED ?? 0);

  const workerFresh = Number(metrics?.workers?.fresh_within_120s ?? 0);
  const workerTotal = Number(metrics?.workers?.total ?? 0);
  const admission = metrics?.workers?.pipeline_admission;
  const stages = runtimeStream?.stages ?? [];

  const onlineWorkers = admission?.online_workers ?? workerFresh;
  const inflight = admission?.total_inflight ?? jobsActivePipeline;
  const maxInflight = admission?.total_capacity ?? 0;
  const utilPct = pipelineUtilizationPct(inflight, maxInflight > 0 ? maxInflight : undefined);
  const celeryUnavailable = metrics?.queue_backlog?.celery_unavailable === true;
  const succeededTotal = Number(jobsByStatus.SUCCEEDED ?? 0);
  const errRate = errorRatePct(succeededTotal, failedJobs);
  const throughputPerMin = history[history.length - 1]?.throughputPerMin ?? null;

  const latencyP95Ms = metrics?.latency?.total_latency_ms?.p95 ?? null;
  const latencyP50Ms = metrics?.latency?.total_latency_ms?.p50 ?? null;
  const slo = metrics?.slo;

  const health = useMemo(
    () =>
      deriveSystemHealth({
        celeryUnavailable,
        onlineWorkers,
        admissionHeadroom: admission?.headroom,
        utilPct,
        failedJobs,
        unmatchedWorkers: unmatchedBrokerWorkers.length,
      }),
    [celeryUnavailable, onlineWorkers, admission?.headroom, utilPct, failedJobs, unmatchedBrokerWorkers.length],
  );

  const failureBuckets = useMemo(
    () =>
      aggregateFailureBuckets(
        jobsByStatus,
        metrics?.model_runs?.by_error_type,
        stages,
        runtimeStream?.retries_recent?.length ?? 0,
      ),
    [jobsByStatus, metrics?.model_runs?.by_error_type, stages, runtimeStream?.retries_recent?.length],
  );

  return (
    <main className="min-h-screen px-4 py-4 sm:px-6">
      <ShowcaseBanner />
      <SystemStatusBar
        verdict={health.verdict}
        reason={health.reason}
        lastUpdatedMs={lastUpdatedMs}
        degradedSources={degradedSources}
        loading={loading}
      />

      <div className="space-y-5">
        {/* SLO + error budget (SRE framing) */}
        <SLOStrip slo={slo} loading={loading} />

        {/* Hero: at-a-glance golden signals with real trends (p95 from SSOT) */}
        <GoldenSignals
          throughputPerMin={throughputPerMin}
          queueDepth={jobsQueued}
          latencyP95Ms={latencyP95Ms}
          latencyP50Ms={latencyP50Ms}
          errorRatePct={errRate}
          failedJobs={failedJobs}
          utilPct={utilPct}
          headroom={admission?.headroom}
          inflight={inflight}
          maxInflight={maxInflight}
          history={history}
          loading={loading}
        />

        {/* Scheduler gate detail */}
        <CapacityAdmissionPanel
          workerTotal={workerTotal}
          runningJobs={inflight}
          queueDepth={jobsQueued}
          admission={admission}
          celeryUnavailable={celeryUnavailable}
          inferenceMaxInflight={metrics?.inference_queue?.max_inflight}
          loading={loading}
        />

        {/* Primary diagnostic: the pipeline spine */}
        <PipelineTopology stages={stages} jobsByStatus={jobsByStatus} loading={loading} />

        {/* Promoted: reliability / dead-letter is incident-critical */}
        <FailureCenter
          buckets={failureBuckets}
          deadLetterItems={deadLetter as DeadLetterJobRow[]}
          loading={loading}
          apiBase={API_BASE}
        />

        <WorkerPoolPanel
          apiBase={API_BASE}
          workers={workers as InfraWorkerRow[]}
          loading={loading}
          admissionHeadroom={admission?.headroom}
          brokerWorkerCount={workersBroker?.worker_count}
          celeryUnavailable={workersBroker?.celery_unavailable}
          unmatchedCount={unmatchedBrokerWorkers.length}
        />

        <ProviderRuntimePanel providers={providers} activeProvider={activeProvider} loading={loading} />

        {/* Cost attribution: token spend + per-model breakdown */}
        {/* Real Apple-Silicon GPU saturation + decode throughput (powermetrics sampler) */}
        <ServingThroughputPanel inferenceQueue={metrics?.inference_queue} cost={costData} loading={loading} />

        <CostAttributionPanel data={costData} loading={loading} />

        {/* Demoted: activity stream is a detail feed, not a headline */}
        <LivePipelineTimeline events={runtimeStream?.events ?? []} loading={loading} limit={20} />

        <JobsTable
          apiBase={API_BASE}
          byStatus={jobsByStatus}
          workers={workers as InfraWorkerRow[]}
          loading={loading}
        />

        {/* Extensions: Agent / RLHF / Prompt — not the production main path */}
        <InfraExperimentsSection>
          <AgentCurationPanel apiBase={API_BASE} />
          <RLHFVotePanel sessionKey={null} apiBase={API_BASE} />
          <PromptExperimentPanel experimentName="prompt_ab_demo" apiBase={API_BASE} />
        </InfraExperimentsSection>
      </div>
    </main>
  );
}
