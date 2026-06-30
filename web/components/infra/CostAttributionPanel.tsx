"use client";

import { ControlPlaneSection } from "./ControlPlaneSection";
import { InfraKpiTile } from "./InfraKpiTile";
import type { InfraSemanticTone } from "@/lib/infraVisualTokens";

// ---------------------------------------------------------------------------
// Types (mirror api/infra_routes.py response shape)
// ---------------------------------------------------------------------------

export type CostTotals = {
  runs?: number;
  runs_with_token_usage?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  total_tokens?: number;
  avg_latency_ms?: number;
  p95_latency_ms?: number;
  est_cost_usd?: number;
  est_cost_per_1k_usd?: number;
  avg_completion_tokens?: number;
};

export type CostModelRow = CostTotals & {
  final_model?: string | null;
};

export type CostData = {
  window_hours?: number;
  since_ts?: number;
  pricing?: {
    input_usd_per_mtok?: number;
    output_usd_per_mtok?: number;
  };
  totals?: CostTotals;
  by_model?: CostModelRow[];
  token_coverage_pct?: number;
};

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function fmtTokens(n?: number | null): string {
  if (n == null || n === 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return String(n);
}

function fmtCost(usd?: number | null): string {
  if (usd == null) return "—";
  if (usd === 0) return "$0.00";
  if (usd < 0.001) return "< $0.001";
  if (usd < 1) return `$${usd.toFixed(3)}`;
  return `$${usd.toFixed(2)}`;
}

/** Shorten "Qwen/Qwen2-VL-7B-Instruct" → "Qwen2-VL-7B-Instruct" */
function fmtModelName(name?: string | null): string {
  if (!name) return "unknown";
  const last = name.split("/").at(-1) ?? name;
  return last.length > 28 ? last.slice(0, 26) + "…" : last;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type Props = {
  data: CostData | null;
  loading?: boolean;
};

export function CostAttributionPanel({ data, loading }: Props) {
  const totals = data?.totals;
  const byModel = data?.by_model ?? [];
  const coverage = data?.token_coverage_pct ?? 0;
  const pricing = data?.pricing;
  const windowHours = data?.window_hours ?? 24;

  const coverageTone: InfraSemanticTone =
    coverage === 0 ? "warning" : coverage >= 80 ? "success" : "neutral";

  const pricingHint =
    pricing && (pricing.input_usd_per_mtok ?? 0) > 0
      ? `$${pricing.input_usd_per_mtok}/$${pricing.output_usd_per_mtok} per 1M tok in/out`
      : "token throughput only (pricing = $0)";

  const promptTok = totals?.prompt_tokens ?? 0;
  const completionTok = totals?.completion_tokens ?? 0;
  const totalTok = totals?.total_tokens ?? promptTok + completionTok;

  return (
    <ControlPlaneSection
      eyebrow="inference cost"
      title="Cost Attribution"
      subtitle={`Token ledger · last ${windowHours >= 168 ? `${(windowHours / 24).toFixed(0)}d` : `${windowHours}h`} · ${pricingHint}`}
      id="cost-attribution"
    >
      {/* ── KPI tiles ── */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        <InfraKpiTile
          label="Total Tokens"
          value={loading ? "…" : fmtTokens(totalTok)}
          hint={
            promptTok > 0
              ? `${fmtTokens(promptTok)} in · ${fmtTokens(completionTok)} out`
              : "no token data yet"
          }
        />

        <InfraKpiTile
          label="Est. Cost"
          value={loading ? "…" : fmtCost(totals?.est_cost_usd)}
          hint="derived at query time · not stored"
          tone={(totals?.est_cost_usd ?? 0) > 1 ? "warning" : "neutral"}
        />

        <InfraKpiTile
          label="Runs"
          value={loading ? "…" : String(totals?.runs ?? 0)}
          hint={`${totals?.runs_with_token_usage ?? 0} with token data`}
        />

        <InfraKpiTile
          label="Token Coverage"
          value={loading ? "…" : `${coverage.toFixed(0)}%`}
          hint="% runs reporting usage"
          tone={coverageTone}
        />
      </div>

      {/* ── Per-model breakdown table ── */}
      {byModel.length > 0 && (
        <div className="mt-5 overflow-x-auto">
          <table className="w-full border-separate border-spacing-0">
            <thead>
              <tr className="text-left">
                {(
                  ["Model", "Runs", "Prompt tok", "Compl. tok", "Est. cost", "Avg lat."] as const
                ).map((h) => (
                  <th
                    key={h}
                    className="border-b border-stroke/60 pb-2 pr-4 font-mono text-[10px] uppercase tracking-[0.16em] text-zinc-600 last:pr-0"
                    style={{ textAlign: h === "Model" ? "left" : "right" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {byModel.map((row, i) => (
                <tr key={i} className="border-b border-stroke/25 last:border-0">
                  <td
                    className="py-2 pr-4 font-mono text-xs text-zinc-300"
                    title={row.final_model ?? ""}
                  >
                    {fmtModelName(row.final_model)}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-zinc-400">
                    {row.runs ?? 0}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-zinc-400">
                    {fmtTokens(row.prompt_tokens)}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums text-zinc-400">
                    {fmtTokens(row.completion_tokens)}
                  </td>
                  <td className="py-2 pr-4 text-right font-mono text-xs tabular-nums font-medium text-zinc-100">
                    {fmtCost(row.est_cost_usd)}
                  </td>
                  <td className="py-2 text-right font-mono text-xs tabular-nums text-zinc-400">
                    {row.avg_latency_ms != null
                      ? `${row.avg_latency_ms.toLocaleString()} ms`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Empty state ── */}
      {byModel.length === 0 && !loading && (
        <p className="mt-4 text-[11px] leading-relaxed text-zinc-600">
          No SUCCEEDED model_runs in this window. Token data is populated when the pipeline runs
          with a provider that reports usage (vLLM / OpenAI-compatible). Ollama requires{" "}
          <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-400">
            /api/generate
          </code>{" "}
          to return <code className="rounded bg-zinc-800 px-1 py-0.5 font-mono text-zinc-400">
            eval_count
          </code>
          .
        </p>
      )}

      {/* ── Pricing footnote ── */}
      <p className="mt-4 text-[10px] leading-relaxed text-zinc-700">
        Cost is estimated from{" "}
        <span className="font-mono">model_runs.prompt_tokens + completion_tokens</span> ×
        configurable per-MTok price.{" "}
        <span className="text-zinc-600">
          Pass <code className="font-mono">?input_usd_per_mtok=0</code> for pure GPU-hour
          tracking.
        </span>
      </p>
    </ControlPlaneSection>
  );
}
