/**
 * Types + helpers for the agentic curation views (single-job Agent Run card and the
 * dashboard "Agentic Curation" panel). Shape mirrors `_build_agent_run_summary` in
 * `api/infra_routes.py`.
 */

export type AgentKeeper = {
  image_id?: string | null;
  score?: number | null;
  confidence?: number | null;
  tier?: string | null;
  escalated?: boolean | null;
  image_path?: string | null;
};

export type AgentStep = {
  action: "analyze" | "finalize";
  image_id?: string | null;
  tier?: string | null;
  score?: number | null;
  confidence?: number | null;
  ok?: boolean | null;
  escalated?: boolean | null;
  reflection?: string | null;
  reason?: string | null;
  step?: number | null;
  latency_ms?: number | null;
  created_at?: number | null;
  selected?: unknown[] | null;
};

export type AgentRunSummary = {
  is_agent_run?: boolean;
  job_id?: number | null;
  job_type?: string | null;
  status?: string | null;
  trace_id?: string | null;
  created_at?: number | null;
  updated_at?: number | null;
  total_latency_ms?: number | null;
  source_dir?: string | null;
  candidate_count?: number | null;
  target_keepers?: number | null;
  max_inferences?: number | null;
  analyzed?: number | null;
  escalated?: number | null;
  selected_count?: number | null;
  metrics?: Record<string, unknown> | null;
  keepers?: AgentKeeper[] | null;
  steps?: AgentStep[] | null;
};

/** Thumbnail URL for a keeper via the FastAPI `/image` endpoint (min max_side is 256). */
export function agentThumbUrl(apiBase: string, imagePath?: string | null, maxSide = 320): string | null {
  if (!imagePath) return null;
  const base = apiBase.replace(/\/$/, "");
  return `${base}/image?path=${encodeURIComponent(imagePath)}&max_side=${maxSide}`;
}

/** Tailwind tone for a 0–100 aesthetic score. */
export function scoreTone(score?: number | null): string {
  if (score == null) return "text-zinc-400";
  if (score >= 80) return "text-emerald-300";
  if (score >= 70) return "text-sky-300";
  if (score >= 55) return "text-amber-300";
  return "text-zinc-400";
}

export function tierBadgeClass(tier?: string | null): string {
  const t = (tier ?? "").toLowerCase();
  if (t === "full") return "border-violet-500/50 bg-violet-950/40 text-violet-200";
  if (t === "fast") return "border-sky-500/40 bg-sky-950/30 text-sky-200";
  return "border-stroke bg-panel2 text-zinc-400";
}

export function formatLatency(ms?: number | null): string {
  if (ms == null || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}

export function shortName(imageId?: string | null): string {
  if (!imageId) return "—";
  const base = imageId.split("/").pop() ?? imageId;
  return base;
}

/** A label like `agent analyze …` / `agent finalize …` identifies an agent decision span. */
export function isAgentSpanLabel(label?: string | null): boolean {
  return typeof label === "string" && label.startsWith("agent ");
}
