/** Shared semantic colors for Infra control plane KPIs and status tiles. */

export type InfraSemanticTone = "success" | "warning" | "failure" | "neutral";

export const INFRA_TONE_BORDER: Record<InfraSemanticTone, string> = {
  success: "border-emerald-500/35 bg-emerald-950/12",
  warning: "border-amber-500/35 bg-amber-950/12",
  failure: "border-red-500/40 bg-red-950/15",
  neutral: "border-stroke/90 bg-panel2/70",
};

export const INFRA_TONE_VALUE: Record<InfraSemanticTone, string> = {
  success: "text-emerald-100",
  warning: "text-amber-100",
  failure: "text-red-200",
  neutral: "text-zinc-50",
};

/** Uniform KPI card shell — use inside responsive grids. */
export const INFRA_KPI_CARD =
  "flex min-h-[7.25rem] flex-col justify-between rounded-xl border p-4 sm:min-h-[7.5rem] sm:p-5";

export const INFRA_KPI_LABEL = "text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500";
export const INFRA_KPI_VALUE = "mt-2 text-4xl font-semibold tabular-nums leading-none tracking-tight sm:text-[2.75rem]";
export const INFRA_KPI_HINT = "mt-auto pt-3 text-[11px] leading-snug text-zinc-600";
