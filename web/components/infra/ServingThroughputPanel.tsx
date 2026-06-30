"use client";

import { useEffect, useRef, useState } from "react";
import type { CostData } from "./CostAttributionPanel";
import type { InfraSemanticTone } from "@/lib/infraVisualTokens";
import { INFRA_TONE_BORDER, INFRA_TONE_VALUE } from "@/lib/infraVisualTokens";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";
import { Sparkline } from "./Sparkline";

// Mirrors metrics.inference_queue from api/infra/metrics (process-local gauges
// emitted by PrioritizedInferenceQueue) — see infra/metrics.py.
export type InferenceQueueSnapshot = {
  max_inflight?: number;
  depth?: number;
  active_workers?: number;
  num_workers?: number;
  gpu_util_estimate_30s?: number | null;
  // Real Apple-Silicon GPU reading (powermetrics sampler) when fresh; see infra/gpu_telemetry.py.
  gpu_util?: number | null; // value to display (real if present, else estimate)
  gpu_util_real?: number | null;
  gpu_util_source?: "powermetrics" | "estimate" | "none" | null;
  gpu_freq_mhz?: number | null;
  gpu_power_w?: number | null;
  gpu_sample_age_sec?: number | null;
  throughput_img_per_sec_30s?: number | null;
  avg_batch_size?: number | null;
  avg_queue_wait_ms?: number | null;
  avg_job_e2e_ms?: number | null;
};

type Props = {
  inferenceQueue: InferenceQueueSnapshot | null | undefined;
  cost: CostData | null;
  loading?: boolean;
};

const SPARK_CAP = 48;
const SPARK_COLOR: Record<InfraSemanticTone, string> = {
  success: "text-emerald-400/80",
  warning: "text-amber-400/80",
  failure: "text-red-400/80",
  neutral: "text-zinc-500/70",
};

/**
 * Estimated *decode* throughput (tokens/sec) for a single in-flight request,
 * derived from the token ledger: avg completion tokens / avg request latency.
 * This is the raw decode speed an interviewer means by "tokens/sec", computed
 * from real model_runs rows rather than a wall-clock guess.
 */
function deriveDecodeTps(cost: CostData | null): number | null {
  const t = cost?.totals;
  const avgTok = t?.avg_completion_tokens ?? 0;
  const avgLatMs = t?.avg_latency_ms ?? 0;
  if (avgTok <= 0 || avgLatMs <= 0) return null;
  return Math.round((avgTok * 1000) / avgLatMs);
}

function gpuTone(pct: number | null): InfraSemanticTone {
  if (pct == null) return "neutral";
  if (pct >= 95) return "failure";
  if (pct >= 75) return "success";
  if (pct > 0) return "warning"; // underutilized GPU == wasted $$
  return "neutral";
}

function MetricCard({
  eyebrow,
  value,
  unit,
  tone,
  primary,
  secondary,
  spark,
  loading,
}: {
  eyebrow: string;
  value: string;
  unit?: string;
  tone: InfraSemanticTone;
  primary: string;
  secondary: string;
  spark?: Array<number | null>;
  loading?: boolean;
}) {
  return (
    <div className={`flex flex-col justify-between rounded-xl border p-4 ${INFRA_TONE_BORDER[tone]}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">{eyebrow}</div>
        {spark ? <Sparkline data={spark} className={SPARK_COLOR[tone]} /> : null}
      </div>
      <div className="mt-2">
        <span className={`text-3xl font-semibold tabular-nums leading-none tracking-tight ${INFRA_TONE_VALUE[tone]}`}>
          {loading ? "…" : value}
        </span>
        {unit ? <span className="ml-1 text-sm text-zinc-500">{unit}</span> : null}
      </div>
      <div className="mt-2 text-[11px] leading-tight text-zinc-500">
        <span className="text-zinc-400">{primary}</span>
        <span className="block text-zinc-600">{secondary}</span>
      </div>
    </div>
  );
}

export function ServingThroughputPanel({ inferenceQueue, cost, loading }: Props) {
  const q = inferenceQueue ?? {};
  // Prefer the real powermetrics reading; fall back to the busy-time estimate.
  const gpuReal = q.gpu_util != null ? q.gpu_util : q.gpu_util_estimate_30s;
  const gpuPct = gpuReal != null ? Math.round(gpuReal * 100) : null;
  const gpuIsReal = q.gpu_util_source === "powermetrics";
  const decodeTps = deriveDecodeTps(cost);
  const imgPerSec = q.throughput_img_per_sec_30s ?? null;
  const active = q.active_workers ?? 0;
  const maxInflight = q.max_inflight ?? 0;
  const concPct = maxInflight > 0 ? Math.round((active / maxInflight) * 100) : null;

  // Self-accumulated trend rings (client-observed since mount) so the serving
  // tier gets sparklines without bloating the page-level history payload.
  const [gpuHist, setGpuHist] = useState<Array<number | null>>([]);
  const [tpsHist, setTpsHist] = useState<Array<number | null>>([]);
  const lastGpu = useRef<number | null>(null);
  const lastTps = useRef<number | null>(null);

  useEffect(() => {
    if (gpuPct === lastGpu.current) return;
    lastGpu.current = gpuPct;
    setGpuHist((p) => [...p, gpuPct].slice(-SPARK_CAP));
  }, [gpuPct]);

  useEffect(() => {
    if (decodeTps === lastTps.current) return;
    lastTps.current = decodeTps;
    setTpsHist((p) => [...p, decodeTps].slice(-SPARK_CAP));
  }, [decodeTps]);

  const concTone: InfraSemanticTone =
    concPct == null ? "neutral" : concPct >= 100 ? "failure" : concPct >= 80 ? "warning" : concPct > 0 ? "success" : "neutral";
  const concBarTone =
    concPct == null ? "bg-zinc-600" : concPct >= 100 ? "bg-red-500" : concPct >= 80 ? "bg-amber-500" : "bg-emerald-500";

  const hasLiveServing = gpuPct != null || imgPerSec != null || active > 0;

  return (
    <ControlPlaneSection
      eyebrow="serving"
      title="GPU Serving Throughput"
      subtitle="VLM tier saturation · decode tokens/sec · batch efficiency — process-local gauges from PrioritizedInferenceQueue + token ledger"
      right={<LivePulse active={hasLiveServing} />}
      id="serving-throughput"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          eyebrow={gpuIsReal ? "GPU Utilization · real" : "GPU Utilization · est"}
          value={gpuPct != null ? String(gpuPct) : "—"}
          unit="%"
          tone={gpuTone(gpuPct)}
          primary={
            gpuIsReal
              ? `Apple GPU${q.gpu_power_w != null ? ` · ${q.gpu_power_w.toFixed(1)}W` : ""}${
                  q.gpu_freq_mhz != null ? ` · ${Math.round(q.gpu_freq_mhz)}MHz` : ""
                }`
              : `${q.num_workers ?? 0} VLM workers`
          }
          secondary={gpuIsReal ? "powermetrics · live" : "busy-time est · 30s window"}
          spark={gpuHist}
          loading={loading}
        />
        <MetricCard
          eyebrow="Decode Throughput"
          value={decodeTps != null ? decodeTps.toLocaleString() : "—"}
          unit="tok/s"
          tone="neutral"
          primary={
            cost?.totals?.avg_completion_tokens
              ? `${Math.round(cost.totals.avg_completion_tokens)} tok/req`
              : "no token data yet"
          }
          secondary="est · completion tok / latency"
          spark={tpsHist}
          loading={loading}
        />
        <MetricCard
          eyebrow="Inference Rate"
          value={imgPerSec != null ? imgPerSec.toFixed(2) : "—"}
          unit="img/s"
          tone={imgPerSec != null && imgPerSec > 0 ? "success" : "neutral"}
          primary={q.avg_batch_size != null ? `batch ${q.avg_batch_size}` : "batch —"}
          secondary="completed / 30s window"
          loading={loading}
        />
        <MetricCard
          eyebrow="VLM Concurrency"
          value={maxInflight > 0 ? `${active}/${maxInflight}` : String(active)}
          tone={concTone}
          primary={concPct != null ? `${concPct}% of inflight cap` : "cap —"}
          secondary={q.avg_queue_wait_ms != null ? `q-wait ${q.avg_queue_wait_ms} ms` : "queue wait —"}
          loading={loading}
        />
      </div>

      {/* Concurrency saturation bar — the GPU admission ceiling for the VLM tier */}
      <div className="mt-4">
        <div className="mb-1.5 flex justify-between text-[10px] font-medium uppercase tracking-wide text-zinc-600">
          <span>Inflight saturation (active / max_inflight)</span>
          <span className="tabular-nums text-zinc-500">{concPct != null ? `${concPct}%` : "—"}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-zinc-800/90">
          <div
            className={`h-full rounded-full transition-all duration-700 ease-out ${concBarTone}`}
            style={{ width: `${concPct != null ? Math.min(100, Math.max(2, concPct)) : 0}%` }}
          />
        </div>
      </div>

      {!hasLiveServing && !loading ? (
        <p className="mt-4 text-[11px] leading-relaxed text-zinc-600">
          No live VLM workers reporting. GPU utilization and inference-rate gauges are{" "}
          <span className="font-mono">process-local</span> — populated when a worker runs{" "}
          <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-400">PrioritizedInferenceQueue</code>.
          Decode tok/s is derived from the persisted <span className="font-mono">model_runs</span> token ledger and
          survives restarts.
        </p>
      ) : null}
    </ControlPlaneSection>
  );
}
