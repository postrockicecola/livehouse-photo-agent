"use client";

import { rollupStageStats } from "./utils";
import type { RuntimeEvent, StageFlowStat } from "./types";

type Props = {
  stages: StageFlowStat[];
  retries: RuntimeEvent[];
  loading?: boolean;
};

function stageTone(running: number, queued: number, failed: number): string {
  if (running > 0) return "border-sky-500/40 bg-sky-950/30 shadow-[0_0_24px_rgba(56,189,248,0.08)]";
  if (failed > 0) return "border-red-500/30 bg-red-950/20";
  if (queued > 0) return "border-amber-500/25 bg-amber-950/15";
  return "border-stroke/70 bg-[#0c0d11]/80";
}

export function FlowGraph({ stages, retries, loading }: Props) {
  const rollup = rollupStageStats(stages);
  const hotStage = rollup.reduce(
    (best, s) => (s.running > (best?.running ?? 0) ? s : best),
    rollup[0] ?? null,
  );

  return (
    <section className="relative overflow-hidden rounded-2xl border border-stroke/80 bg-[#08090c]">
      <div className="runtime-grid pointer-events-none absolute inset-0 opacity-25" />
      <div className="relative border-b border-stroke/60 px-4 py-3 sm:px-5">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-xs uppercase tracking-[0.22em] text-zinc-500">Pipeline orchestration</h2>
            <p className="mt-1 text-sm text-zinc-400">Job lifecycle · stage flow · scheduling lanes</p>
          </div>
          {hotStage && hotStage.running > 0 ? (
            <div className="font-mono text-[11px] text-sky-300/90">
              active bottleneck · <span className="text-sky-200">{hotStage.label}</span>
            </div>
          ) : null}
        </div>
      </div>

      <div className="relative overflow-x-auto px-3 py-5 sm:px-5">
        {loading && !stages.length ? (
          <div className="py-10 text-center font-mono text-xs text-zinc-600">loading stage graph…</div>
        ) : (
          <div className="flex min-w-max items-stretch gap-0">
            {rollup.map((stage, i) => (
              <div key={stage.key} className="flex items-stretch">
                <div
                  className={`relative w-[7.5rem] rounded-xl border px-3 py-3 transition-colors duration-500 sm:w-[8.5rem] ${stageTone(
                    stage.running,
                    stage.queued,
                    stage.failed,
                  )}`}
                >
                  {stage.running > 0 ? (
                    <span className="runtime-flow-pulse absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-sky-400" />
                  ) : null}
                  <div className="font-mono text-[11px] font-medium tracking-wide text-zinc-200">{stage.label}</div>
                  <div className="mt-2 space-y-1 font-mono text-[10px] leading-tight">
                    <div className="flex justify-between text-sky-300/90">
                      <span>run</span>
                      <span className="tabular-nums">{stage.running}</span>
                    </div>
                    <div className="flex justify-between text-amber-200/80">
                      <span>queue</span>
                      <span className="tabular-nums">{stage.queued}</span>
                    </div>
                    <div className="flex justify-between text-red-300/80">
                      <span>fail</span>
                      <span className="tabular-nums">{stage.failed}</span>
                    </div>
                    <div className="flex justify-between border-t border-stroke/40 pt-1 text-zinc-500">
                      <span>avg</span>
                      <span className="tabular-nums">
                        {stage.avgLatencyMs != null ? `${stage.avgLatencyMs}ms` : "—"}
                      </span>
                    </div>
                  </div>
                </div>
                {i < rollup.length - 1 ? (
                  <div className="flex w-8 items-center justify-center sm:w-10">
                    <div className="runtime-flow-line h-px w-full bg-gradient-to-r from-stroke via-sky-500/40 to-stroke" />
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      {retries.length > 0 ? (
        <div className="border-t border-stroke/60 px-4 py-3 sm:px-5">
          <div className="mb-2 text-[10px] uppercase tracking-[0.18em] text-zinc-600">Recent retries</div>
          <div className="flex flex-wrap gap-2">
            {retries.slice(-6).map((r) => (
              <span
                key={r.id}
                className="rounded-md border border-amber-500/20 bg-amber-950/20 px-2 py-1 font-mono text-[10px] text-amber-200/80"
              >
                #{r.job_id} · {(r.message ?? "retry").slice(0, 48)}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  );
}
