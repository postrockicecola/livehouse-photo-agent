"use client";

import {
  agentThumbUrl,
  formatLatency,
  scoreTone,
  shortName,
  tierBadgeClass,
  type AgentRunSummary,
  type AgentStep,
} from "@/lib/agentRun";

type Props = {
  agent: AgentRunSummary;
  apiBase: string;
};

function Metric({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-lg border border-stroke bg-panel2 px-3 py-2">
      <div className="font-mono text-[10px] uppercase tracking-wide text-zinc-600">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold tabular-nums ${tone ?? "text-zinc-100"}`}>{value}</div>
    </div>
  );
}

function AnalyzeRow({ step }: { step: AgentStep }) {
  const esc = !!step.escalated;
  const failed = step.ok === false;
  return (
    <li
      className={`flex items-center gap-3 rounded-lg border px-3 py-2 ${
        esc ? "border-violet-500/40 bg-violet-950/20" : "border-stroke/70 bg-panel2/60"
      }`}
    >
      <span className="font-mono text-[11px] text-zinc-600 w-8 shrink-0">#{step.step ?? "—"}</span>
      <span className="shrink-0">
        {esc ? (
          <span className="inline-flex items-center gap-1 rounded-md border border-violet-500/50 bg-violet-950/40 px-1.5 py-0.5 text-[10px] font-medium text-violet-200">
            ↑ escalate
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded-md border border-sky-500/30 bg-sky-950/20 px-1.5 py-0.5 text-[10px] text-sky-300/90">
            analyze
          </span>
        )}
      </span>
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-300" title={step.image_id ?? ""}>
        {shortName(step.image_id)}
      </span>
      <span className={`shrink-0 rounded-md border px-1.5 py-0.5 text-[10px] ${tierBadgeClass(step.tier)}`}>
        {step.tier ?? "—"}
      </span>
      <span className={`w-16 shrink-0 text-right font-mono text-xs tabular-nums ${failed ? "text-red-300" : scoreTone(step.score)}`}>
        {failed ? "err" : step.score != null ? Number(step.score).toFixed(1) : "—"}
      </span>
      <span className="w-14 shrink-0 text-right font-mono text-[11px] tabular-nums text-zinc-500">
        {step.confidence != null ? `c${Number(step.confidence).toFixed(2)}` : "—"}
      </span>
      <span className="w-12 shrink-0 text-right font-mono text-[11px] tabular-nums text-zinc-600">
        {formatLatency(step.latency_ms)}
      </span>
    </li>
  );
}

export function AgentRunCard({ agent, apiBase }: Props) {
  const keepers = agent.keepers ?? [];
  const steps = agent.steps ?? [];
  const analyzeSteps = steps.filter((s) => s.action === "analyze");
  const finalize = steps.find((s) => s.action === "finalize");
  const analyzed = agent.analyzed ?? analyzeSteps.length;
  const escalated = agent.escalated ?? analyzeSteps.filter((s) => s.escalated).length;
  const escRate = analyzed > 0 ? Math.round((escalated / analyzed) * 100) : 0;
  const running = (agent.status ?? "") !== "SUCCEEDED" && !String(agent.status ?? "").startsWith("FAILED");

  return (
    <section className="glass mb-4 rounded-2xl border border-violet-500/30 bg-violet-950/5 p-4 sm:p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-md border border-violet-500/50 bg-violet-950/40 px-2 py-1 text-xs font-semibold text-violet-200">
            <span aria-hidden>🤖</span> Agentic Curation
          </span>
          <h2 className="text-base font-semibold text-zinc-100">observe → plan → act → reflect</h2>
        </div>
        <div className="text-xs text-zinc-500">
          {agent.job_type} · budget {agent.max_inferences ?? "—"} inferences · target {agent.target_keepers ?? "—"} keepers
        </div>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-5">
        <Metric label="candidates" value={agent.candidate_count ?? "—"} />
        <Metric label="analyzed" value={analyzed} tone="text-sky-200" />
        <Metric label="escalations" value={`${escalated}${analyzed ? ` · ${escRate}%` : ""}`} tone="text-violet-200" />
        <Metric label="keepers" value={agent.selected_count ?? keepers.length} tone="text-emerald-300" />
        <Metric label="wall time" value={formatLatency(agent.total_latency_ms)} />
      </div>

      {keepers.length > 0 ? (
        <div className="mb-4">
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wide text-zinc-600">
            selected keepers ({keepers.length})
          </div>
          <div className="flex flex-wrap gap-3">
            {keepers.map((k, i) => {
              const url = agentThumbUrl(apiBase, k.image_path);
              return (
                <div key={`${k.image_id ?? i}`} className="w-28">
                  <div className="relative aspect-[3/2] overflow-hidden rounded-lg border border-stroke bg-zinc-900">
                    {url ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={url} alt={k.image_id ?? ""} className="h-full w-full object-cover" loading="lazy" />
                    ) : (
                      <div className="flex h-full items-center justify-center text-[10px] text-zinc-600">no preview</div>
                    )}
                    {k.escalated ? (
                      <span className="absolute left-1 top-1 rounded bg-violet-600/80 px-1 text-[9px] font-medium text-white">
                        ↑full
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 flex items-center justify-between">
                    <span className={`font-mono text-xs font-semibold tabular-nums ${scoreTone(k.score)}`}>
                      {k.score != null ? Number(k.score).toFixed(1) : "—"}
                    </span>
                    <span className="font-mono text-[10px] text-zinc-600">{k.tier ?? ""}</span>
                  </div>
                  <div className="truncate font-mono text-[10px] text-zinc-500" title={k.image_id ?? ""}>
                    {shortName(k.image_id)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="mb-4 rounded-lg border border-stroke/70 bg-panel2/50 px-3 py-2 text-xs text-zinc-500">
          {running ? "Agent running — no keepers selected yet." : "Agent selected 0 keepers (none cleared the keep threshold)."}
        </div>
      )}

      {analyzeSteps.length > 0 ? (
        <div>
          <div className="mb-2 font-mono text-[10px] uppercase tracking-wide text-zinc-600">
            decision trace ({analyzeSteps.length} analyze{escalated ? `, ${escalated} escalated` : ""})
          </div>
          <ul className="space-y-1">
            {analyzeSteps.map((s, i) => (
              <AnalyzeRow key={`${s.step ?? i}-${s.image_id ?? i}`} step={s} />
            ))}
            {finalize ? (
              <li className="flex items-center gap-3 rounded-lg border border-emerald-500/40 bg-emerald-950/20 px-3 py-2">
                <span className="font-mono text-[11px] text-zinc-600 w-8 shrink-0">#{finalize.step ?? "—"}</span>
                <span className="inline-flex items-center gap-1 rounded-md border border-emerald-500/50 bg-emerald-950/40 px-1.5 py-0.5 text-[10px] font-medium text-emerald-200">
                  ✓ finalize
                </span>
                <span className="text-xs text-zinc-300">
                  selected {Array.isArray(finalize.selected) ? finalize.selected.length : agent.selected_count ?? 0} keepers
                </span>
              </li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
