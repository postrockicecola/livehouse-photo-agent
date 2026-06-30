"use client";

import type { InfraSloWindow } from "@/app/infra/page";

type Props = {
  slo: InfraSloWindow | undefined;
  loading?: boolean;
};

function fmtWindow(sec: number | undefined): string {
  if (!sec) return "—";
  if (sec % 3600 === 0) return `${sec / 3600}h`;
  if (sec % 60 === 0) return `${sec / 60}m`;
  return `${sec}s`;
}

export function SLOStrip({ slo, loading }: Props) {
  const target = slo?.target_pct ?? 99;
  const rate = slo?.success_rate_pct ?? null;
  const budget = slo?.error_budget_remaining_pct ?? null;
  const completed = slo?.completed ?? 0;

  const meets = rate != null && rate >= target;
  const rateTone = rate == null ? "text-zinc-500" : meets ? "text-emerald-300" : "text-red-300";
  const budgetTone =
    budget == null ? "bg-zinc-600" : budget >= 50 ? "bg-emerald-500" : budget >= 20 ? "bg-amber-500" : "bg-red-500";

  return (
    <section className="glass flex flex-wrap items-center justify-between gap-x-6 gap-y-3 rounded-2xl border border-stroke px-4 py-3 sm:px-5">
      <div className="flex items-center gap-6">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-600">SLO · success rate</div>
          <div className="mt-0.5 flex items-baseline gap-2">
            <span className={`text-2xl font-semibold tabular-nums ${rateTone}`}>
              {loading ? "…" : rate != null ? `${rate}%` : "—"}
            </span>
            <span className="text-xs text-zinc-600">target {target}%</span>
          </div>
        </div>
        <div className="hidden text-xs text-zinc-500 sm:block">
          <div>
            window <span className="text-zinc-300">{fmtWindow(slo?.window_sec)}</span>
          </div>
          <div>
            {completed} completed · <span className="text-emerald-300/80">{slo?.succeeded ?? 0} ok</span> ·{" "}
            <span className="text-red-300/80">{slo?.failed ?? 0} fail</span>
          </div>
        </div>
      </div>

      <div className="min-w-[180px] flex-1 sm:max-w-xs">
        <div className="mb-1 flex justify-between text-[10px] font-medium uppercase tracking-wide text-zinc-600">
          <span>Error budget left</span>
          <span className="tabular-nums text-zinc-400">{budget != null ? `${budget}%` : "—"}</span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-zinc-800/90">
          <div
            className={`h-full rounded-full transition-all duration-700 ease-out ${budgetTone}`}
            style={{ width: `${budget != null ? Math.min(100, Math.max(0, budget)) : 0}%` }}
          />
        </div>
      </div>
    </section>
  );
}
