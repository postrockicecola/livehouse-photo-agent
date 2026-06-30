"use client";

import { useEffect, useRef, useState } from "react";
import type { InfraWorkerRow } from "@/components/WorkersPanel";
import type { InfraSemanticTone } from "@/lib/infraVisualTokens";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";
import { Sparkline } from "./Sparkline";

// ---------------------------------------------------------------------------
// KEDA ScaledObject config — mirrors deploy/k8s/60-keda-scaledobject.yaml and
// 61-keda-vlm.yaml (single source of truth lives in those manifests). KEDA
// drives: desiredReplicas = clamp(ceil(LLEN(queue) / listLength), min, max).
// ---------------------------------------------------------------------------

type PoolConfig = {
  name: string;
  queue: string;
  listLength: number;
  min: number;
  max: number;
  isVlm: boolean;
};

const POOLS: PoolConfig[] = [
  { name: "worker-general", queue: "celery", listLength: 5, min: 1, max: 6, isVlm: false },
  { name: "worker-vlm", queue: "vlm", listLength: 3, min: 1, max: 4, isVlm: true },
];

const POLL_INTERVAL_SEC = 5; // KEDA pollingInterval
const COOLDOWN_SEC = 30; // KEDA cooldownPeriod
const SPARK_CAP = 48;

export type AutoscalingBacklog = {
  redis_list_len?: number | null;
  active?: number;
  reserved?: number;
  scheduled?: number;
  celery_unavailable?: boolean;
};

type Props = {
  queueBacklog: AutoscalingBacklog | null | undefined;
  vlmQueueDepth: number | null | undefined;
  workers: InfraWorkerRow[];
  loading?: boolean;
};

function clamp(n: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, n));
}

function poolReplicas(workers: InfraWorkerRow[], isVlm: boolean): { current: number; online: number } {
  let current = 0;
  let online = 0;
  for (const w of workers) {
    const type = (w.worker_type ?? "").toLowerCase();
    const matches = isVlm ? /vlm/.test(type) : !/vlm/.test(type);
    if (!matches) continue;
    const st = (w.status ?? "").toUpperCase();
    if (st !== "OFFLINE") current += 1; // a pod still exists while paused/draining
    if (st === "ONLINE") online += 1;
  }
  return { current, online };
}

type Verdict = { label: string; tone: InfraSemanticTone };

function deriveVerdict(backlog: number, cfg: PoolConfig, current: number, desired: number, down: boolean): Verdict {
  if (down) return { label: "broker offline", tone: "failure" };
  if (desired >= cfg.max && backlog / cfg.listLength > cfg.max) return { label: "saturated · at max", tone: "failure" };
  if (desired > current) return { label: "scaling up", tone: "warning" };
  if (desired < current) return { label: "scaling down", tone: "neutral" };
  return { label: "steady", tone: "success" };
}

function PoolCard({
  cfg,
  backlog,
  workers,
  brokerDown,
  loading,
}: {
  cfg: PoolConfig;
  backlog: number;
  workers: InfraWorkerRow[];
  brokerDown: boolean;
  loading?: boolean;
}) {
  const { current, online } = poolReplicas(workers, cfg.isVlm);
  const desired = brokerDown ? current : clamp(Math.ceil(backlog / cfg.listLength), cfg.min, cfg.max);
  const verdict = deriveVerdict(backlog, cfg, current, desired, brokerDown);
  const perReplica = current > 0 ? (backlog / current).toFixed(1) : "—";

  // Client-accumulated backlog trend (KEDA's trigger metric over time).
  const [hist, setHist] = useState<Array<number | null>>([]);
  const last = useRef<number | null>(null);
  useEffect(() => {
    if (backlog === last.current) return;
    last.current = backlog;
    setHist((p) => [...p, backlog].slice(-SPARK_CAP));
  }, [backlog]);

  const toneText: Record<InfraSemanticTone, string> = {
    success: "text-emerald-300",
    warning: "text-amber-300",
    failure: "text-red-300",
    neutral: "text-zinc-400",
  };
  const toneBorder: Record<InfraSemanticTone, string> = {
    success: "border-emerald-500/40 bg-emerald-950/15",
    warning: "border-amber-500/40 bg-amber-950/15",
    failure: "border-red-500/45 bg-red-950/20",
    neutral: "border-stroke/80 bg-panel2/50",
  };

  // min..max replica track with current + desired markers.
  const span = Math.max(1, cfg.max - cfg.min);
  const pos = (n: number) => `${(clamp(n, cfg.min, cfg.max) - cfg.min) / span * 100}%`;

  return (
    <div className={`rounded-xl border p-4 ${toneBorder[verdict.tone]}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-sm font-semibold text-zinc-100">{cfg.name}</span>
            <span className="rounded border border-stroke/70 px-1.5 py-0.5 font-mono text-[10px] text-zinc-400">
              {cfg.isVlm ? "GPU / VLM" : "CPU / general"}
            </span>
          </div>
          <div className="mt-1 font-mono text-[10px] text-zinc-600">
            trigger: ceil(LLEN(<span className="text-zinc-400">{cfg.queue}</span>) / {cfg.listLength}) · clamp [{cfg.min},{cfg.max}]
          </div>
        </div>
        <span className={`rounded-full border border-current px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${toneText[verdict.tone]}`}>
          {loading ? "…" : verdict.label}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-600">Backlog</div>
          <div className="flex items-baseline gap-1">
            <span className="text-2xl font-semibold tabular-nums text-zinc-100">{loading ? "…" : backlog}</span>
            <Sparkline data={hist} className="text-sky-400/70" width={64} height={22} />
          </div>
          <div className="text-[10px] text-zinc-600">{perReplica} / replica</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-600">Current</div>
          <div className="text-2xl font-semibold tabular-nums text-zinc-100">{loading ? "…" : current}</div>
          <div className="text-[10px] text-zinc-600">{online} online</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-wide text-zinc-600">Desired</div>
          <div className={`text-2xl font-semibold tabular-nums ${desired !== current ? toneText[verdict.tone] : "text-zinc-100"}`}>
            {loading ? "…" : desired}
          </div>
          <div className="text-[10px] text-zinc-600">KEDA target</div>
        </div>
      </div>

      {/* Replica track: min ──current/desired── max */}
      <div className="mt-3">
        <div className="relative h-1.5 rounded-full bg-zinc-800/90">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-zinc-600/70"
            style={{ width: pos(current) }}
          />
          <div
            className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-zinc-950 bg-zinc-200"
            style={{ left: pos(current) }}
            title={`current ${current}`}
          />
          <div
            className={`absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 border border-zinc-950 ${
              verdict.tone === "warning" ? "bg-amber-400" : verdict.tone === "failure" ? "bg-red-400" : "bg-emerald-400"
            }`}
            style={{ left: pos(desired) }}
            title={`desired ${desired}`}
          />
        </div>
        <div className="mt-1 flex justify-between font-mono text-[9px] text-zinc-700">
          <span>min {cfg.min}</span>
          <span>max {cfg.max}</span>
        </div>
      </div>
    </div>
  );
}

export function AutoscalingPanel({ queueBacklog, vlmQueueDepth, workers, loading }: Props) {
  const brokerDown = queueBacklog?.celery_unavailable === true;
  const generalBacklog =
    queueBacklog?.redis_list_len ??
    (Number(queueBacklog?.active ?? 0) + Number(queueBacklog?.reserved ?? 0) + Number(queueBacklog?.scheduled ?? 0));
  const vlmBacklog = Number(vlmQueueDepth ?? 0);

  const backlogFor = (cfg: PoolConfig) => (cfg.isVlm ? vlmBacklog : Number(generalBacklog ?? 0));

  return (
    <ControlPlaneSection
      eyebrow="autoscaling"
      title="Queue-Driven Autoscaling (KEDA)"
      subtitle={`desired = clamp(ceil(queue_depth / listLength), min, max) · poll ${POLL_INTERVAL_SEC}s · cooldown ${COOLDOWN_SEC}s — computed from the same Redis backlog KEDA reads`}
      right={<LivePulse active={!brokerDown} />}
      id="autoscaling"
    >
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {POOLS.map((cfg) => (
          <PoolCard
            key={cfg.name}
            cfg={cfg}
            backlog={backlogFor(cfg)}
            workers={workers}
            brokerDown={brokerDown}
            loading={loading}
          />
        ))}
      </div>
      <p className="mt-4 text-[10px] leading-relaxed text-zinc-700">
        Replica targets use KEDA&apos;s HPA formula against live queue depth; <span className="text-zinc-500">current</span>{" "}
        reflects live worker rows in the SSOT. In-cluster these converge to KEDA&apos;s actual scaling decision (subject to
        the {COOLDOWN_SEC}s scale-down cooldown). Config mirrors{" "}
        <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-400">deploy/k8s/60-keda-scaledobject.yaml</code>.
      </p>
    </ControlPlaneSection>
  );
}
