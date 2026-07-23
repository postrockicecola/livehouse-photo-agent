"use client";

import { formatLatencyMs, type InfraHistoryPoint } from "@/lib/infraControlPlane";
import type { InfraSemanticTone } from "@/lib/infraVisualTokens";
import { INFRA_TONE_BORDER, INFRA_TONE_VALUE } from "@/lib/infraVisualTokens";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";
import { Sparkline } from "./Sparkline";

type Props = {
  throughputPerMin: number | null;
  queueDepth: number;
  latencyP95Ms: number | null;
  latencyP50Ms: number | null;
  errorRatePct: number | null;
  failedJobs: number;
  utilPct: number | null;
  headroom: number | undefined;
  inflight: number;
  maxInflight: number;
  history: InfraHistoryPoint[];
  loading?: boolean;
};

const SPARK_COLOR: Record<InfraSemanticTone, string> = {
  success: "text-emerald-400/80",
  warning: "text-amber-400/80",
  failure: "text-red-400/80",
  neutral: "text-zinc-500/70",
};

function SignalCard({
  eyebrow,
  value,
  unit,
  tone,
  primaryLabel,
  secondary,
  spark,
  loading,
}: {
  eyebrow: string;
  value: string;
  unit?: string;
  tone: InfraSemanticTone;
  primaryLabel: string;
  secondary: string;
  spark: Array<number | null>;
  loading?: boolean;
}) {
  return (
    <div className={`flex flex-col justify-between rounded-xl border p-4 ${INFRA_TONE_BORDER[tone]}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">{eyebrow}</div>
        <Sparkline data={spark} className={SPARK_COLOR[tone]} />
      </div>
      <div className="mt-2">
        <span className={`text-3xl font-semibold tabular-nums leading-none tracking-tight ${INFRA_TONE_VALUE[tone]}`}>
          {loading ? "…" : value}
        </span>
        {unit ? <span className="ml-1 text-sm text-zinc-500">{unit}</span> : null}
      </div>
      <div className="mt-2 text-[11px] leading-tight text-zinc-500">
        <span className="text-zinc-400">{primaryLabel}</span>
        <span className="block text-zinc-600">{secondary}</span>
      </div>
    </div>
  );
}

export function GoldenSignals({
  throughputPerMin,
  queueDepth,
  latencyP95Ms,
  latencyP50Ms,
  errorRatePct,
  failedJobs,
  utilPct,
  headroom,
  inflight,
  maxInflight,
  history,
  loading,
}: Props) {
  const trafficTone: InfraSemanticTone = queueDepth > 50 ? "warning" : "neutral";
  const errorTone: InfraSemanticTone =
    (errorRatePct ?? 0) >= 5 || failedJobs > 0 ? (errorRatePct != null && errorRatePct >= 20 ? "failure" : "warning") : "success";
  const satTone: InfraSemanticTone =
    (utilPct ?? 0) >= 100 ? "failure" : (utilPct ?? 0) >= 85 ? "warning" : (utilPct ?? 0) > 0 ? "success" : "neutral";

  const errSpark = history.map((h) => {
    const done = h.succeededCumulative + h.failedTotal;
    return done > 0 ? Math.round((h.failedTotal / done) * 1000) / 10 : null;
  });

  return (
    <ControlPlaneSection
      id="tour-signals"
      eyebrow="Overview"
      title="Golden Signals"
      subtitle="Traffic · Latency · Errors · Saturation — sparklines reflect samples since page open"
      right={<LivePulse />}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <SignalCard
          eyebrow="Traffic"
          value={throughputPerMin != null ? String(throughputPerMin) : "—"}
          unit="jobs/min"
          tone={trafficTone}
          primaryLabel={`${queueDepth} queued`}
          secondary="completed rate · queue depth"
          spark={history.map((h) => h.throughputPerMin)}
          loading={loading}
        />
        <SignalCard
          eyebrow="Latency"
          value={formatLatencyMs(latencyP95Ms)}
          tone="neutral"
          primaryLabel="pipeline p95"
          secondary={`p50 ${formatLatencyMs(latencyP50Ms)} · total_latency_ms`}
          spark={history.map((h) => h.pipelineAvgMs)}
          loading={loading}
        />
        <SignalCard
          eyebrow="Errors"
          value={errorRatePct != null ? String(errorRatePct) : "0"}
          unit="%"
          tone={errorTone}
          primaryLabel={`${failedJobs} failed now`}
          secondary="failed / completed (session)"
          spark={errSpark}
          loading={loading}
        />
        <SignalCard
          eyebrow="Saturation"
          value={utilPct != null ? String(utilPct) : "—"}
          unit="%"
          tone={satTone}
          primaryLabel={maxInflight > 0 ? `${inflight}/${maxInflight} inflight` : `${inflight} inflight`}
          secondary={headroom != null ? `${headroom} slots free` : "headroom —"}
          spark={history.map((h) => h.utilPct)}
          loading={loading}
        />
      </div>
    </ControlPlaneSection>
  );
}
