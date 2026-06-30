"use client";

import {
  admissionOpen,
  pipelineHeadroomLabel,
  pipelineUtilizationPct,
} from "@/lib/infraControlPlane";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";

function CompactStat({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-lg border border-stroke/80 bg-panel2/50 px-3 py-2.5">
      <div className="text-[10px] font-medium uppercase tracking-wide text-zinc-600">{label}</div>
      <div className="mt-0.5 text-lg font-semibold tabular-nums text-zinc-100">{value}</div>
      {hint ? <div className="text-[10px] text-zinc-600">{hint}</div> : null}
    </div>
  );
}

export type PipelineAdmissionSnapshot = {
  headroom?: number;
  total_capacity?: number;
  total_inflight?: number;
  online_workers?: number;
  total_worker_rows?: number;
};

type Props = {
  workerTotal: number;
  runningJobs: number;
  queueDepth: number;
  admission: PipelineAdmissionSnapshot | null | undefined;
  celeryUnavailable: boolean;
  inferenceMaxInflight?: number | null;
  loading?: boolean;
};

export function CapacityAdmissionPanel({
  workerTotal,
  runningJobs,
  queueDepth,
  admission,
  celeryUnavailable,
  inferenceMaxInflight,
  loading,
}: Props) {
  const headroom = admission?.headroom;
  const maxInflight = admission?.total_capacity ?? 0;
  const inflight = admission?.total_inflight ?? runningJobs;
  const online = admission?.online_workers;
  const util = pipelineUtilizationPct(inflight, maxInflight > 0 ? maxInflight : undefined);
  const adm = admissionOpen(headroom, celeryUnavailable);
  const headroomLine = pipelineHeadroomLabel(headroom, maxInflight > 0 ? maxInflight : undefined);

  const admissionBannerCls = adm.open
    ? adm.tone === "warn"
      ? "border-amber-500/45 bg-amber-950/20"
      : "border-emerald-500/40 bg-emerald-950/18"
    : "border-red-500/50 bg-red-950/28 ring-1 ring-red-500/20";

  const utilBarPct = util ?? 0;
  const utilTone =
    utilBarPct >= 100 || !adm.open ? "bg-red-500" : utilBarPct >= 85 ? "bg-amber-500" : "bg-emerald-500";

  return (
    <ControlPlaneSection
      eyebrow="Scheduler"
      title="Capacity & Admission"
      subtitle="Dispatch gate closes when inflight reaches worker capacity"
      right={<LivePulse active={!celeryUnavailable} />}
    >
      <div
        className={`mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3.5 transition-colors duration-500 ${admissionBannerCls}`}
      >
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-600">Admission</p>
          <p
            className={`mt-1 text-xl font-semibold tracking-tight sm:text-2xl ${
              adm.open ? (adm.tone === "warn" ? "text-amber-100" : "text-emerald-100") : "text-red-200"
            }`}
          >
            {loading ? "…" : adm.shortLabel}
          </p>
        </div>
        <div className="text-right">
          <p className="font-mono text-xs text-zinc-400">{loading ? "…" : headroomLine}</p>
          {online != null ? (
            <p className="mt-1 text-[11px] text-zinc-600">
              {online} online · {workerTotal} total
            </p>
          ) : null}
        </div>
      </div>

      <div className="mb-4">
        <div className="mb-1.5 flex justify-between text-[10px] font-medium uppercase tracking-wide text-zinc-600">
          <span>Pool utilization</span>
          <span className="tabular-nums text-zinc-500">
            {loading || util == null ? "—" : `${util}%`}
            {!loading && maxInflight > 0 ? ` · ${inflight}/${maxInflight}` : ""}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-zinc-800/90">
          <div
            className={`h-full rounded-full transition-all duration-700 ease-out ${utilTone}`}
            style={{ width: `${Math.min(100, Math.max(adm.open ? 2 : 100, utilBarPct))}%` }}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-5">
        <CompactStat
          label="Workers"
          value={loading ? "…" : workerTotal}
          hint={online != null ? `${online} online` : undefined}
        />
        <CompactStat label="Inflight" value={loading ? "…" : inflight} hint="on ONLINE pool" />
        <CompactStat label="Max inflight" value={loading ? "…" : maxInflight > 0 ? maxInflight : "—"} hint="ONLINE capacity" />
        <CompactStat label="Queue depth" value={loading ? "…" : queueDepth} hint="QUEUED in SSOT" />
        <CompactStat
          label="VLM inflight cap"
          value={loading ? "…" : inferenceMaxInflight != null && inferenceMaxInflight > 0 ? inferenceMaxInflight : "—"}
          hint="separate queue"
        />
      </div>
    </ControlPlaneSection>
  );
}
