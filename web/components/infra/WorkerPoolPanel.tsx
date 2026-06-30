"use client";

import { useState } from "react";
import type { InfraWorkerRow } from "@/components/WorkersPanel";
import { ControlPlaneSection } from "./ControlPlaneSection";

const HEARTBEAT_FRESH_SEC = 120;

function formatHeartbeat(ts?: number | null): { text: string; stale: boolean } {
  if (!ts) return { text: "—", stale: true };
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  let text: string;
  if (delta < 60) text = `${delta}s ago`;
  else if (delta < 3600) text = `${Math.floor(delta / 60)}m ago`;
  else text = `${Math.floor(delta / 3600)}h ago`;
  return { text, stale: delta > HEARTBEAT_FRESH_SEC };
}

function inferCurrentStage(w: InfraWorkerRow): string {
  const tasks = w.celery_broker?.active_tasks ?? [];
  if (tasks.length > 0) {
    const name = tasks[0]?.name ?? "";
    if (name.includes("stage1") || name.includes("filter")) return "Stage 1";
    if (name.includes("stage2") || name.includes("score")) return "Stage 2";
    if (name.includes("stage3") || name.includes("vlm") || name.includes("infer")) return "Stage 3";
    if (name.includes("artifact") || name.includes("export") || name.includes("finalize")) return "Export";
    return name.split(".").pop() ?? "Running";
  }
  const pool = (w.worker_type ?? "").toUpperCase();
  if (pool.includes("STAGE3") || pool.includes("VLM")) return "Stage 3 pool";
  if (pool.includes("STAGE2")) return "Stage 2 pool";
  if (pool.includes("STAGE1")) return "Stage 1 pool";
  if ((w.inflight ?? 0) > 0) return "Pipeline";
  return "Idle";
}

type Props = {
  apiBase: string;
  workers: InfraWorkerRow[];
  loading?: boolean;
  admissionHeadroom?: number;
  brokerWorkerCount?: number;
  celeryUnavailable?: boolean;
  unmatchedCount?: number;
};

type WorkerAction = "pause" | "resume" | "drain";

const ACTION_LABEL: Record<WorkerAction, string> = { pause: "Pause", resume: "Resume", drain: "Drain" };

function SlotBar({ inflight, capacity }: { inflight: number; capacity: number | null }) {
  const cap = capacity && capacity > 0 ? capacity : null;
  const pct = cap ? Math.min(100, Math.round((inflight / cap) * 100)) : inflight > 0 ? 100 : 0;
  const tone = pct >= 100 ? "bg-red-500/70" : pct >= 85 ? "bg-amber-500/70" : "bg-emerald-500/70";
  return (
    <div className="flex items-center gap-2 text-[10px] text-zinc-500">
      <span className="w-12 uppercase">slots</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-zinc-800">
        <div className={`h-full rounded-full ${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-12 text-right font-mono tabular-nums text-zinc-400">
        {inflight}/{cap ?? "—"}
      </span>
    </div>
  );
}

export function WorkerPoolPanel({
  apiBase,
  workers,
  loading,
  admissionHeadroom,
  brokerWorkerCount,
  celeryUnavailable,
  unmatchedCount = 0,
}: Props) {
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const runAction = async (workerId: number, action: WorkerAction) => {
    if (!window.confirm(`${ACTION_LABEL[action]} worker #${workerId}?`)) return;
    const key = `${workerId}:${action}`;
    setBusyKey(key);
    setNotice(null);
    try {
      const r = await fetch(`${apiBase}/api/infra/workers/${workerId}/${action}`, { method: "POST" });
      const j = (await r.json().catch(() => ({}))) as { detail?: string; status?: string };
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`);
      setNotice(`worker #${workerId} → ${j.status ?? ACTION_LABEL[action]}`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : `${action} failed`);
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <ControlPlaneSection
      id="workers"
      eyebrow="Compute"
      title="Worker Pool"
      subtitle="SQLite worker rows correlated with Celery inspect · CPU/MEM shown as local placeholders until host metrics wire-up"
      right={
        <div className="text-right font-mono text-[10px] text-zinc-500">
          <div>headroom {admissionHeadroom ?? "—"}</div>
          <div>{celeryUnavailable ? "broker unreachable" : `celery ${brokerWorkerCount ?? 0} online`}</div>
        </div>
      }
    >
      {notice ? (
        <div className="mb-3 rounded border border-stroke/80 bg-zinc-900/60 px-3 py-2 text-xs text-zinc-300">{notice}</div>
      ) : null}
      {loading ? (
        <div className="py-10 text-sm text-zinc-400">Loading workers…</div>
      ) : workers.length === 0 ? (
        <div className="py-8 text-sm text-zinc-500">No workers registered</div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {workers.map((w, idx) => {
            const key = String(w.worker_name ?? w.id ?? idx);
            const hb = formatHeartbeat(w.last_heartbeat);
            const st = (w.status ?? "").toUpperCase();
            const online = st === "ONLINE" && !hb.stale;
            const running = (w.inflight ?? 0) + (w.celery_broker?.active_count ?? 0);
            const statusDot = online ? "bg-emerald-400" : st === "DRAINING" ? "bg-amber-400" : "bg-zinc-600";

            return (
              <div key={key} className="rounded-xl border border-stroke bg-panel2/70 p-4">
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className={`h-2 w-2 rounded-full ${statusDot}`} />
                      <span className="font-medium text-zinc-100">{w.worker_name ?? `worker-${w.id ?? idx}`}</span>
                    </div>
                    <div className="mt-1 font-mono text-[10px] text-zinc-500">
                      {online ? "ONLINE" : st || "UNKNOWN"}
                      {w.worker_type ? ` · ${w.worker_type}` : ""}
                    </div>
                  </div>
                  <div className="text-right text-xs tabular-nums text-zinc-400">
                    <div>{running} running</div>
                    <div className={hb.stale ? "text-amber-300/90" : ""}>♥ {hb.text}</div>
                  </div>
                </div>
                <div className="mt-3 text-xs text-zinc-500">
                  Stage <span className="text-zinc-300">{inferCurrentStage(w)}</span>
                  <span className="text-zinc-600">
                    {" "}
                    · inflight {w.inflight ?? 0}/{w.capacity ?? "—"}
                  </span>
                </div>
                <div className="mt-3 space-y-1.5">
                  <SlotBar inflight={w.inflight ?? 0} capacity={w.capacity ?? null} />
                  <p className="text-[10px] text-zinc-700">CPU/MEM: host metrics not wired up</p>
                </div>
                {w.id != null ? (
                  <div className="mt-3 flex flex-wrap gap-1.5 border-t border-stroke/60 pt-3">
                    {(["pause", "drain", "resume"] as const).map((action) => {
                      const wid = w.id as number;
                      const aKey = `${wid}:${action}`;
                      const disabled = busyKey === aKey || (action === "resume" && online);
                      return (
                        <button
                          key={action}
                          type="button"
                          disabled={disabled}
                          onClick={() => runAction(wid, action)}
                          className="rounded border border-stroke bg-panel2 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
                        >
                          {busyKey === aKey ? "…" : ACTION_LABEL[action]}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      )}
      {unmatchedCount > 0 ? (
        <p className="mt-3 text-xs text-amber-200/80">
          {unmatchedCount} Celery worker(s) online without a matching SQLite row — see broker section in metrics.
        </p>
      ) : null}
    </ControlPlaneSection>
  );
}
