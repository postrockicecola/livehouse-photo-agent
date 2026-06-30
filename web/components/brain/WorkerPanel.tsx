"use client";

import type { InfraWorkerRow } from "./types";

const HEARTBEAT_FRESH_SEC = 120;

type Props = {
  workers: InfraWorkerRow[];
  admission?: {
    headroom?: number;
    total_capacity?: number;
    total_inflight?: number;
    online_workers?: number;
  } | null;
  loading?: boolean;
};

function heartbeatMeta(ts?: number | null): { label: string; fresh: boolean; pct: number } {
  if (!ts) return { label: "no signal", fresh: false, pct: 0 };
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  const fresh = delta <= HEARTBEAT_FRESH_SEC;
  const pct = fresh ? Math.max(0.15, 1 - delta / HEARTBEAT_FRESH_SEC) : 0.08;
  let label: string;
  if (delta < 60) label = `${delta}s`;
  else if (delta < 3600) label = `${Math.floor(delta / 60)}m`;
  else label = `${Math.floor(delta / 3600)}h`;
  return { label, fresh, pct };
}

function statusColor(st?: string): string {
  const s = (st ?? "").toUpperCase();
  if (s === "ONLINE") return "text-emerald-300";
  if (s === "DRAINING") return "text-amber-300";
  if (s === "PAUSED") return "text-zinc-500";
  if (s === "OFFLINE" || s === "ERROR") return "text-red-300/90";
  return "text-zinc-500";
}

export function WorkerPanel({ workers, admission, loading }: Props) {
  return (
    <section className="rounded-2xl border border-stroke/80 bg-[#08090c] p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-2">
        <div>
          <h2 className="text-xs uppercase tracking-[0.22em] text-zinc-500">Workers</h2>
          <p className="mt-1 text-sm text-zinc-400">distributed executors · heartbeat · capacity</p>
        </div>
        {admission ? (
          <div className="text-right font-mono text-[10px] text-zinc-600">
            headroom {admission.headroom ?? "—"} · cap {admission.total_capacity ?? "—"} · inflight{" "}
            {admission.total_inflight ?? "—"}
          </div>
        ) : null}
      </div>

      {loading && !workers.length ? (
        <div className="py-6 font-mono text-xs text-zinc-600">scanning worker nodes…</div>
      ) : !workers.length ? (
        <div className="py-6 font-mono text-xs text-zinc-600">no workers registered</div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {workers.map((w, idx) => {
            const hb = heartbeatMeta(w.last_heartbeat);
            const cap = w.capacity ?? 0;
            const inf = w.inflight ?? 0;
            const loadPct = cap > 0 ? Math.min(1, inf / cap) : 0;
            return (
              <div
                key={`${w.id ?? "w"}-${idx}`}
                className="group relative overflow-hidden rounded-xl border border-stroke/70 bg-[#0a0b0f] p-3 transition-colors hover:border-stroke"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-sm text-zinc-200">
                      {w.worker_name ?? `worker-${w.id ?? idx}`}
                    </div>
                    <div className="mt-0.5 font-mono text-[10px] text-zinc-600">
                      {w.worker_type ?? "default"} executor
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`runtime-pulse-dot h-2 w-2 rounded-full ${
                        hb.fresh && w.status === "ONLINE" ? "bg-emerald-400" : "bg-zinc-600"
                      }`}
                    />
                    <span className={`font-mono text-[10px] uppercase ${statusColor(w.status)}`}>
                      {w.status ?? "unknown"}
                    </span>
                  </div>
                </div>

                <div className="mt-3 grid grid-cols-3 gap-2 font-mono text-[10px]">
                  <div>
                    <div className="text-zinc-600">inflight</div>
                    <div className="tabular-nums text-zinc-300">{inf}</div>
                  </div>
                  <div>
                    <div className="text-zinc-600">capacity</div>
                    <div className="tabular-nums text-zinc-300">{cap || "—"}</div>
                  </div>
                  <div>
                    <div className="text-zinc-600">heartbeat</div>
                    <div className={`tabular-nums ${hb.fresh ? "text-emerald-300/80" : "text-amber-300/80"}`}>
                      {hb.label}
                    </div>
                  </div>
                </div>

                <div className="mt-3 h-1 overflow-hidden rounded-full bg-zinc-900">
                  <div
                    className="runtime-worker-bar h-full rounded-full bg-gradient-to-r from-sky-600/80 to-sky-400/60 transition-all duration-700"
                    style={{ width: `${Math.max(loadPct * 100, hb.pct * 40)}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
