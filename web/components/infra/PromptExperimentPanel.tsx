"use client";

import { useCallback, useEffect, useState } from "react";
import { ControlPlaneSection } from "./ControlPlaneSection";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type VariantSummary = {
  variant_id: number;
  variant_name: string;
  variant_tag: string;
  runs: number;
  runs_with_score: number;
  avg_score: number | null;
  avg_latency_ms: number | null;
  avg_prompt_tokens: number | null;
  avg_completion_tokens: number | null;
  win_rate_vs_control: number | null;
};

type ExperimentResults = {
  experiment_name: string;
  window_hours: number;
  variants: VariantSummary[];
};

type Variant = {
  id: number;
  name: string;
  description?: string;
  variant_tag: string;
  prompt_text: string;
  active: number;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const API_BASE =
  typeof window !== "undefined" && window.location.port === "3000"
    ? "http://127.0.0.1:8080"
    : "";

function tagBadge(tag: string) {
  const color =
    tag === "control"
      ? "bg-zinc-700 text-zinc-300"
      : tag.startsWith("treatment")
      ? "bg-indigo-900/50 text-indigo-300 border border-indigo-700/50"
      : "bg-zinc-800 text-zinc-400";
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-widest ${color}`}>
      {tag}
    </span>
  );
}

function winRateBar(rate: number | null, isControl: boolean) {
  if (isControl) return <span className="text-zinc-600 text-xs">—</span>;
  if (rate == null) return <span className="text-zinc-600 text-xs">n/a</span>;
  const pct = Math.round(rate * 100);
  const color = pct >= 60 ? "text-emerald-400" : pct >= 40 ? "text-amber-400" : "text-red-400";
  return <span className={`font-mono text-xs font-semibold ${color}`}>{pct}%</span>;
}

function fmt(n: number | null | undefined, decimals = 1): string {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PromptExperimentPanel({
  experimentName = "prompt_ab_demo",
  apiBase,
}: {
  experimentName?: string;
  apiBase?: string;
}) {
  const base = apiBase ?? API_BASE;
  const [results, setResults] = useState<ExperimentResults | null>(null);
  const [variants, setVariants] = useState<Variant[]>([]);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [windowHours, setWindowHours] = useState(168);

  const fetchData = useCallback(async () => {
    try {
      const [rRes, vRes] = await Promise.all([
        fetch(`${base}/api/experiments/results?experiment_name=${encodeURIComponent(experimentName)}&window_hours=${windowHours}`),
        fetch(`${base}/api/experiments/variants?active_only=false`),
      ]);
      const rData: ExperimentResults = await rRes.json();
      const vData: { variants: Variant[] } = await vRes.json();
      setResults(rData);
      setVariants(vData.variants ?? []);
    } catch {}
  }, [base, experimentName, windowHours]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const promptFor = (id: number) =>
    variants.find((v) => v.id === id)?.prompt_text ?? "";

  const controlAvg =
    results?.variants.find((v) => v.variant_tag === "control")?.avg_score ?? null;

  const windowLabel = windowHours >= 168 ? `${Math.round(windowHours / 24)}d` : `${windowHours}h`;

  return (
    <ControlPlaneSection
      eyebrow="Prompt A/B · Infra Experiment"
      title="Experiment Results"
      subtitle={`Comparing prompt variants · ${results?.variants.length ?? 0} variants · last ${windowLabel} · experimental surface`}
      right={
        <select
          value={windowHours}
          onChange={(e) => setWindowHours(Number(e.target.value))}
          className="rounded border border-zinc-700 bg-zinc-800 px-2 py-1 font-mono text-[11px] text-zinc-300"
        >
          {[24, 72, 168, 720].map((h) => (
            <option key={h} value={h}>{h < 168 ? `${h}h` : `${h / 24}d`}</option>
          ))}
        </select>
      }
    >
      {!results || results.variants.length === 0 ? (
        <p className="py-6 text-center text-sm text-zinc-500">
          No experiment runs yet. Use <code className="text-indigo-400">POST /api/experiments/runs</code> to log results.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-zinc-700/50 text-[10px] uppercase tracking-widest text-zinc-500">
                <th className="pb-2 pr-3">Variant</th>
                <th className="pb-2 pr-3">Tag</th>
                <th className="pb-2 pr-3">Runs</th>
                <th className="pb-2 pr-3">Avg Score</th>
                <th className="pb-2 pr-3">vs Control</th>
                <th className="pb-2 pr-3">Avg Latency</th>
                <th className="pb-2 pr-3">Avg Tokens</th>
                <th className="pb-2">Prompt</th>
              </tr>
            </thead>
            <tbody>
              {results.variants.map((v) => {
                const delta =
                  v.avg_score != null && controlAvg != null && v.variant_tag !== "control"
                    ? v.avg_score - controlAvg
                    : null;
                const deltaStr =
                  delta == null
                    ? null
                    : delta >= 0
                    ? `+${delta.toFixed(1)}`
                    : delta.toFixed(1);
                const deltaColor =
                  delta == null ? "" : delta >= 0 ? "text-emerald-400" : "text-red-400";
                const isExp = expanded === v.variant_id;

                return (
                  <>
                    <tr
                      key={v.variant_id}
                      className="border-b border-zinc-800/60 hover:bg-zinc-800/30 cursor-pointer"
                      onClick={() => setExpanded(isExp ? null : v.variant_id)}
                    >
                      <td className="py-2 pr-3 font-mono text-zinc-200">
                        <span className="mr-1 text-zinc-600">{isExp ? "▾" : "▸"}</span>
                        {v.variant_name}
                      </td>
                      <td className="py-2 pr-3">{tagBadge(v.variant_tag)}</td>
                      <td className="py-2 pr-3 font-mono text-zinc-400">{v.runs}</td>
                      <td className="py-2 pr-3">
                        <span className="font-mono font-semibold text-zinc-200">
                          {fmt(v.avg_score)}
                        </span>
                        {deltaStr && (
                          <span className={`ml-1 font-mono text-[10px] ${deltaColor}`}>
                            ({deltaStr})
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-3">{winRateBar(v.win_rate_vs_control, v.variant_tag === "control")}</td>
                      <td className="py-2 pr-3 font-mono text-zinc-400">
                        {v.avg_latency_ms != null
                          ? `${(v.avg_latency_ms / 1000).toFixed(1)}s`
                          : "—"}
                      </td>
                      <td className="py-2 pr-3 font-mono text-zinc-400">
                        {v.avg_prompt_tokens != null
                          ? `${Math.round(v.avg_prompt_tokens)}p / ${Math.round(v.avg_completion_tokens ?? 0)}c`
                          : "—"}
                      </td>
                      <td className="py-2 max-w-[120px]">
                        <span
                          className="line-clamp-1 text-[10px] text-zinc-500"
                          title={promptFor(v.variant_id)}
                        >
                          {promptFor(v.variant_id).slice(0, 60)}…
                        </span>
                      </td>
                    </tr>
                    {isExp && (
                      <tr key={`${v.variant_id}-exp`} className="bg-zinc-900/70">
                        <td colSpan={8} className="px-4 py-3">
                          <p className="mb-1 text-[10px] font-bold uppercase tracking-widest text-zinc-500">
                            Full Prompt Text
                          </p>
                          <pre className="max-h-36 overflow-y-auto whitespace-pre-wrap rounded border border-zinc-700 bg-zinc-800 p-3 font-mono text-[11px] text-zinc-300 leading-relaxed">
                            {promptFor(v.variant_id)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Legend */}
      <div className="mt-3 flex flex-wrap gap-4 border-t border-zinc-800/60 pt-3">
        <span className="text-[10px] text-zinc-600">
          <span className="text-emerald-400">win rate</span> = % of treatment runs scoring above control avg
        </span>
        <span className="text-[10px] text-zinc-600">
          <span className="text-zinc-300">Tokens</span> = prompt / completion
        </span>
      </div>
    </ControlPlaneSection>
  );
}
