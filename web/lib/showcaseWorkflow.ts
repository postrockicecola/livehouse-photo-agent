/**
 * Showcase-only funnel shaping: catalog rows often have flat counts
 * (imported == filtered == scored). Rematerialize a monotonic keep funnel so
 * Studio workflow doesn't look like a no-op.
 */

export type ShowcaseFunnel = {
  imported: number;
  filtered: number;
  scored: number;
  picked: number;
  exported: number;
};

export type ShowcaseWorkflowStage = {
  label: string;
  count: number;
  state: "done" | "active" | "pending" | "failed";
};

/** Keep-rate export when archive funnel is flat / missing. */
export function synthExported(imported: number, sessionSeed = 0): number {
  if (imported <= 0) return 0;
  const rate = 0.06 + ((Math.abs(sessionSeed) % 9) * 0.01);
  return Math.max(1, Math.round(imported * rate));
}

/** Monotonic Imported ≥ Filtered ≥ Scored ≥ Picked ≥ Exported. */
export function taperShowcaseFunnel(
  importedRaw: number,
  exportedRaw?: number | null,
  sessionSeed = 0,
): ShowcaseFunnel {
  const imported = Math.max(0, Math.floor(importedRaw || 0));
  let exported = Math.floor(exportedRaw ?? 0);
  if (imported <= 0) {
    return { imported: 0, filtered: 0, scored: 0, picked: 0, exported: 0 };
  }
  if (exported <= 0 || exported >= imported * 0.5) {
    exported = synthExported(imported, sessionSeed);
  }
  exported = Math.min(imported, Math.max(1, exported));

  let filtered = Math.max(exported, Math.round(imported * 0.79));
  let scored = Math.max(exported, Math.round(filtered * 0.58));
  let picked = Math.max(exported, Math.min(scored, Math.round(exported * 1.25)));

  filtered = Math.min(imported, Math.max(filtered, scored));
  scored = Math.min(filtered, Math.max(scored, picked));
  picked = Math.min(scored, Math.max(picked, exported));

  return { imported, filtered, scored, picked, exported };
}

export function workflowStagesFromFunnel(
  funnel: ShowcaseFunnel,
  state: ShowcaseWorkflowStage["state"] = "done",
): ShowcaseWorkflowStage[] {
  return [
    { label: "Imported", count: funnel.imported, state },
    { label: "Filtered", count: funnel.filtered, state },
    { label: "AI Scored", count: funnel.scored, state },
    { label: "Picked", count: funnel.picked, state },
    { label: "Exported", count: funnel.exported, state },
  ];
}

export function pipelineStagesFromFunnel(funnel: ShowcaseFunnel) {
  return [
    {
      label: "PREPARE",
      state: "done" as const,
      count_in: funnel.imported,
      count_out: funnel.imported,
      duration_sec: null,
    },
    {
      label: "S1",
      state: "done" as const,
      count_in: funnel.imported,
      count_out: funnel.filtered,
      duration_sec: null,
    },
    {
      label: "S2",
      state: "done" as const,
      count_in: funnel.filtered,
      count_out: funnel.scored,
      duration_sec: null,
    },
    {
      label: "S3",
      state: "done" as const,
      count_in: funnel.scored,
      count_out: funnel.scored,
      duration_sec: null,
    },
    {
      label: "WRITE",
      state: "done" as const,
      count_in: funnel.scored,
      count_out: funnel.exported,
      duration_sec: null,
    },
  ];
}

type SessionLike = {
  session_key?: string;
  session_dir?: string;
  previews_dir?: string;
  preview_count?: number;
  photos_ingested?: number;
  brain_session_id?: number | null;
  session_date?: string;
  band_name?: string;
  has_analysis_results?: boolean;
  funnel?: {
    imported?: number | null;
    filtered?: number | null;
    scored?: number | null;
    picked?: number | null;
    exported?: number | null;
  } | null;
};

/** Overlay a representative status snapshot with the selected session's tapered funnel. */
export function applyShowcaseStatusOverlay(
  status: Record<string, unknown>,
  session: SessionLike | null | undefined,
): Record<string, unknown> {
  if (!session) return status;
  const imported =
    Number(session.funnel?.imported) ||
    Number(session.preview_count) ||
    Number(session.photos_ingested) ||
    0;
  const seed = Number(session.brain_session_id) || 0;
  const funnel = taperShowcaseFunnel(imported, session.funnel?.exported, seed);

  const sessionBlock = {
    session_key: session.session_key,
    session_dir: session.session_dir,
    previews_dir: session.previews_dir,
    preview_count: funnel.imported,
    has_analysis_results: session.has_analysis_results ?? true,
    brain_session_id: session.brain_session_id ?? null,
    activity: "analyzed",
  };

  const pipeline = (status.pipeline && typeof status.pipeline === "object"
    ? { ...(status.pipeline as Record<string, unknown>) }
    : {}) as Record<string, unknown>;

  return {
    ...status,
    active: {
      session_key: session.session_key,
      session_dir: session.session_dir,
      previews_dir: session.previews_dir,
      preview_count: funnel.imported,
      has_analysis_results: session.has_analysis_results ?? true,
      session_date: session.session_date,
      band_name: session.band_name,
      funnel,
      photos_ingested: funnel.imported,
    },
    session: sessionBlock,
    pipeline: {
      ...pipeline,
      labels: pipeline.labels ?? ["PREPARE", "S1", "S2", "S3", "WRITE"],
      current_index: 4,
      complete: true,
      failed: false,
      stages: pipelineStagesFromFunnel(funnel),
      workflow_stages: workflowStagesFromFunnel(funnel, "done"),
    },
  };
}
