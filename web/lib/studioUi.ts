import type { StudioStatusResponse } from "@/lib/studioApi";
import { getApiBase } from "@/lib/apiBase";

export const PIPELINE_DISPLAY_LABELS = ["PREPARE", "STAGE1", "STAGE2", "STAGE3", "WRITE"] as const;

export const PHOTOGRAPHY_WORKFLOW_LABELS = [
  "Imported",
  "Filtered",
  "AI Scored",
  "Picked",
  "Exported",
] as const;

export type PhotographyWorkflowNode = {
  label: string;
  count: number | null;
  state: "done" | "active" | "pending" | "failed";
};

/** Photography funnel for Workbench pipeline (API `workflow_stages` or derived from dev stages). */
export function buildPhotographyWorkflowViews(
  pipeline: StudioStatusResponse["pipeline"],
  events: StudioStatusResponse["events"],
  previewCount: number,
): PhotographyWorkflowNode[] {
  const api = pipeline.workflow_stages;
  if (api?.length) {
    return api.map((s) => ({
      label: s.label,
      count: s.count ?? null,
      state: s.state,
    }));
  }

  const dev = buildPipelineStageViews(pipeline, events, previewCount);
  const counts: (number | null)[] = [
    dev[0]?.count_out ?? dev[0]?.count_in ?? (previewCount > 0 ? previewCount : null),
    dev[1]?.count_out ?? null,
    dev[3]?.count_out ?? dev[2]?.count_out ?? null,
    dev[4]?.count_in ?? dev[3]?.count_out ?? null,
    dev[4]?.count_out ?? null,
  ];

  let hi = -1;
  if (pipeline.complete) hi = 4;
  else {
    const cur = pipeline.current_index;
    if (cur === 0) hi = 0;
    else if (cur === 1) hi = 1;
    else if (cur === 2 || cur === 3) hi = 2;
    else if (cur >= 4) hi = 3;
  }

  return PHOTOGRAPHY_WORKFLOW_LABELS.map((label, i) => {
    let state: PhotographyWorkflowNode["state"] = "pending";
    if (pipeline.complete) state = "done";
    else if (pipeline.failed && hi === i) state = "failed";
    else if (hi < 0) state = "pending";
    else if (i < hi) state = "done";
    else if (i === hi) state = "active";
    return { label, count: counts[i] ?? null, state };
  });
}

export function photographyWorkflowHint(label: string): string | null {
  if (label === "Filtered") return "Passed filter";
  return null;
}

const API_LABEL_TO_DISPLAY: Record<string, string> = {
  PREPARE: "PREPARE",
  S1: "STAGE1",
  S2: "STAGE2",
  S3: "STAGE3",
  WRITE: "WRITE",
};

export function pipelineDisplayLabel(apiLabel: string, index: number): string {
  return API_LABEL_TO_DISPLAY[apiLabel] ?? PIPELINE_DISPLAY_LABELS[index] ?? apiLabel;
}

export function formatDeliveryPhotos(n: number): string {
  if (n < 0) return "—";
  return `${n.toLocaleString("en-US")} photos exported`;
}

export function sessionDateFromKey(sessionKey: string): string {
  if (sessionKey.length >= 10 && sessionKey[4] === "-" && sessionKey[7] === "-") {
    return sessionKey.slice(0, 10);
  }
  return sessionKey;
}

export function formatElapsed(sec: number | null | undefined): string {
  if (sec == null || sec < 0) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m > 0) return `${m}m${s.toString().padStart(2, "0")}s`;
  return `${s}s`;
}

export function formatPhotosStat(n: number): string {
  if (n >= 1000) {
    const rounded = Math.floor(n / 1000) * 1000;
    return `${rounded.toLocaleString("en-US")}+`;
  }
  if (n >= 100) return `${Math.floor(n / 100) * 100}+`;
  return String(n);
}

/** Studio stats headline: exact count or rounded thousands with +. */
export function formatScaleStat(n: number, loading = false): string {
  if (loading) return "…";
  if (n <= 0) return "0";
  return formatPhotosStat(n);
}

export function formatStatSessions(n: number, loading = false): string {
  if (loading) return "…";
  return n.toLocaleString("en-US");
}

/** Showcase hero: rounded tens with + for session scale (e.g. 52 → 50+). */
export function formatShowcaseSessions(n: number, loading = false): string {
  if (loading) return "…";
  if (n <= 0) return "0";
  if (n >= 1000) return formatPhotosStat(n);
  if (n >= 50) return `${Math.floor(n / 10) * 10}+`;
  if (n >= 10) return `${Math.floor(n / 5) * 5}+`;
  return String(n);
}

export function formatStatPercent(pct: number | null | undefined, loading = false): string {
  if (loading) return "…";
  if (pct == null || pct < 0) return "—";
  return `${pct}%`;
}

/** e.g. 7m09s */
export function formatAvgProcessingTime(sec: number | null | undefined, loading = false): string {
  if (loading) return "…";
  if (sec == null || sec <= 0) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m > 0) return `${m}m${s.toString().padStart(2, "0")}s`;
  return `${s}s`;
}

export function formatRuntimeHours(
  hours: number | null | undefined,
  runtimeSec: number | null | undefined,
  loading = false,
): string {
  if (loading) return "…";
  let h: number | null = null;
  if (hours != null && hours > 0) h = hours;
  else if (runtimeSec != null && runtimeSec > 0) h = runtimeSec / 3600;
  if (h == null || h <= 0) return "—";
  if (h >= 10) return `${Math.round(h)}h`;
  return `${h.toFixed(1).replace(/\.0$/, "")}h`;
}

export function buildStudioCoverUrl(coverPathQuoted: string | undefined, maxSide = 160): string | null {
  const pq = coverPathQuoted?.trim();
  if (!pq) return null;
  const base = getApiBase();
  return `${base}/image?path=${pq}&max_side=${maxSide}`;
}

type StageStat = {
  label: string;
  state: "done" | "active" | "pending" | "failed";
  count_in?: number | null;
  count_out?: number | null;
  duration_sec?: number | null;
};

export function formatPipelineDuration(sec: number): string {
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return formatElapsed(Math.round(sec));
}

export function buildPipelineStageViews(
  pipeline: StudioStatusResponse["pipeline"],
  events: StudioStatusResponse["events"],
  previewCount: number,
): StageStat[] {
  const apiStages = pipeline.stages;
  if (apiStages?.length) {
    return apiStages.map((s) => ({
      label: s.label,
      state: s.state,
      count_in: s.count_in,
      count_out: s.count_out,
      duration_sec: s.duration_sec,
    }));
  }
  return buildPipelineStageStatsFallback(pipeline, events, previewCount);
}

function buildPipelineStageStatsFallback(
  pipeline: StudioStatusResponse["pipeline"],
  events: StudioStatusResponse["events"],
  previewCount: number,
): StageStat[] {
  const labels = pipeline.labels?.length ? pipeline.labels : [...PIPELINE_DISPLAY_LABELS];
  const cur = pipeline.current_index;
  const legacy = buildPipelineStageStatsLegacy(events, previewCount);

  return labels.map((label, i) => {
    let state: StageStat["state"] = "pending";
    if (pipeline.complete) state = "done";
    else if (pipeline.failed && cur === i) state = "failed";
    else if (cur >= 0 && i < cur) state = "done";
    else if (cur === i) state = "active";

    const leg = legacy[i];
    let count_in: number | null = null;
    let count_out: number | null = null;
    if (i === 0) {
      count_out = previewCount > 0 ? previewCount : leg?.images ?? null;
      count_in = count_out;
    } else if (i === 4) {
      count_out = leg?.images ?? null;
    } else {
      count_out = leg?.images ?? null;
    }

    return {
      label,
      state,
      count_in,
      count_out,
      duration_sec: leg?.durationSec ?? null,
    };
  });
}

type LegacyStageStat = { images?: number; durationSec?: number };

function parseEventPayload(raw: unknown): Record<string, unknown> {
  if (!raw) return {};
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw === "string") {
    try {
      const v = JSON.parse(raw) as unknown;
      return typeof v === "object" && v && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
    } catch {
      return {};
    }
  }
  return {};
}

const STAGE_NAME_TO_INDEX: Record<string, number> = {
  PREPARE_INPUT: 0,
  STAGE1_FILTER: 1,
  STAGE2_FAST_SCORE: 2,
  STAGE3_VLM: 3,
  WRITE_ARTIFACT: 4,
};

function buildPipelineStageStatsLegacy(
  events: StudioStatusResponse["events"],
  previewCount: number,
): LegacyStageStat[] {
  const stats: LegacyStageStat[] = PIPELINE_DISPLAY_LABELS.map(() => ({}));
  if (previewCount > 0) stats[0].images = previewCount;

  let stageStart: Record<number, number> = {};
  for (const ev of events) {
    const ts = ev.created_at ?? null;
    const payload = parseEventPayload(ev.payload_json);
    const stageName = String(payload.stage_name ?? "").trim();
    const idx = STAGE_NAME_TO_INDEX[stageName];
    if (idx != null && ts != null) {
      if (stageStart[idx] == null) stageStart[idx] = ts;
      const images =
        num(payload.images_processed) ??
        num(payload.image_count) ??
        num(payload.images_entering_stage3) ??
        num(payload.after);
      if (images != null) stats[idx].images = images;
    }
    const msg = String(ev.message ?? "");
    for (const [name, i] of Object.entries(STAGE_NAME_TO_INDEX)) {
      if (msg.includes(name) && ts != null) {
        if (stageStart[i] == null) stageStart[i] = ts;
      }
    }
  }

  const ordered = events
    .filter((e) => e.created_at != null)
    .slice()
    .sort((a, b) => (a.created_at ?? 0) - (b.created_at ?? 0));

  for (let i = 0; i < PIPELINE_DISPLAY_LABELS.length; i++) {
    const start = stageStart[i];
    if (start == null) continue;
    const nextStart = Object.entries(stageStart)
      .map(([k, v]) => ({ idx: Number(k), ts: v }))
      .filter((x) => x.idx > i && x.ts > start)
      .sort((a, b) => a.ts - b.ts)[0]?.ts;
    const end =
      nextStart ??
      ordered.filter((e) => (e.created_at ?? 0) > start).slice(-1)[0]?.created_at ??
      start;
    if (end > start) stats[i].durationSec = end - start;
  }

  return stats;
}

function num(v: unknown): number | undefined {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim()) {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return undefined;
}

export function formatEventTime(createdAt: number | null): string {
  if (createdAt == null) return "—:—";
  const d = new Date(createdAt * 1000);
  const hh = d.getHours().toString().padStart(2, "0");
  const mm = d.getMinutes().toString().padStart(2, "0");
  return `${hh}:${mm}`;
}

export type PipelineBusinessEvent = {
  id: string;
  at: number | null;
  title: string;
  detail: string | null;
  durationSec: number | null;
  kind: "queued" | "stage" | "export" | "failed" | "running";
};

const STAGE_EVENT_NAMES = [
  "",
  "STAGE1_FILTER",
  "STAGE2_FAST_SCORE",
  "STAGE3_VLM",
  "WRITE_ARTIFACT",
] as const;

function fmtCount(n: number): string {
  return n.toLocaleString("en-US");
}

function stageDetailLine(index: number, stage: StageStat): string | null {
  if (index === 4) {
    const n = stage.count_out ?? stage.count_in;
    return n != null && n >= 0 ? `${fmtCount(n)} photos` : null;
  }
  if (index === 1 || index === 2 || index === 3) {
    const a = stage.count_in;
    const b = stage.count_out;
    if (a != null && b != null) return `${fmtCount(a)} → ${fmtCount(b)}`;
    if (b != null) return fmtCount(b);
  }
  if (index === 0) {
    const n = stage.count_out ?? stage.count_in;
    return n != null ? `${fmtCount(n)} images` : null;
  }
  return null;
}

function eventTimeForStageIndex(
  index: number,
  sorted: StudioStatusResponse["events"],
): number | null {
  const want = STAGE_EVENT_NAMES[index];
  for (const ev of sorted) {
    const payload = parseEventPayload(ev.payload_json);
    const sn = String(payload.stage_name ?? "").trim();
    if (want && sn === want && ev.created_at != null) return ev.created_at;
  }
  for (const ev of sorted) {
    const msg = String(ev.message ?? "");
    if (want && msg.includes(want) && ev.created_at != null) return ev.created_at;
  }
  if (index === 1) {
    const ev = sorted.find((e) => e.to_status === "PREPROCESSING");
    if (ev?.created_at != null) return ev.created_at;
  }
  if (index === 2 || index === 3) {
    const ev = sorted.find((e) => e.to_status === "INFERENCING");
    if (ev?.created_at != null) return ev.created_at;
  }
  if (index === 4) {
    const ev = sorted.find((e) => e.to_status === "SUCCEEDED" || e.to_status === "POSTPROCESSING");
    if (ev?.created_at != null) return ev.created_at;
  }
  return null;
}

function estimateStageTimestamp(
  index: number,
  stages: StageStat[],
  sorted: StudioStatusResponse["events"],
): number | null {
  const direct = eventTimeForStageIndex(index, sorted);
  if (direct != null) return direct;

  const queued = sorted.find((e) => e.to_status === "QUEUED");
  const succeeded = sorted.find((e) => e.to_status === "SUCCEEDED");
  const start = queued?.created_at ?? sorted[0]?.created_at ?? null;
  const end = succeeded?.created_at ?? sorted[sorted.length - 1]?.created_at ?? null;

  if (start == null) return null;

  let cursor = start;
  for (let i = 1; i <= index; i++) {
    const dur = stages[i]?.duration_sec;
    if (dur != null && dur > 0) {
      cursor += Math.round(dur);
    } else if (end != null && i === index && index === 4) {
      return end;
    } else if (end != null && start < end) {
      const span = end - start;
      cursor = start + Math.round((span * i) / 4);
    }
  }
  return cursor > start ? cursor : start + index;
}

/** Business-facing pipeline timeline (counts + durations), not raw job audit strings. */
export function buildRecentPipelineEvents(
  events: StudioStatusResponse["events"],
  pipeline: StudioStatusResponse["pipeline"],
  previewCount: number,
): PipelineBusinessEvent[] {
  if (!events.length) return [];

  const sorted = [...events].sort((a, b) => (a.created_at ?? 0) - (b.created_at ?? 0));
  const stages = buildPipelineStageViews(pipeline, events, previewCount);
  const out: PipelineBusinessEvent[] = [];

  const failed = sorted.find((e) =>
    ["FAILED_PERMANENT", "FAILED_RETRYABLE", "DEAD_LETTERED", "CANCELLED"].includes(String(e.to_status ?? "")),
  );

  const queued = sorted.find((e) => e.to_status === "QUEUED");
  if (queued) {
    out.push({
      id: `queued-${queued.id}`,
      at: queued.created_at,
      title: "Job Queued",
      detail: stageDetailLine(0, stages[0]) ?? (previewCount > 0 ? `${fmtCount(previewCount)} images` : null),
      durationSec: null,
      kind: "queued",
    });
  }

  const stageTitles: Record<number, string> = {
    1: "Stage1 Complete",
    2: "Stage2 Complete",
    3: "Stage3 Complete",
    4: "Export Generated",
  };

  for (let i = 1; i <= 4; i++) {
    const stage = stages[i];
    if (!stage) continue;

    const detail = stageDetailLine(i, stage);
    const hasFunnel = detail != null;
    const isDone = stage.state === "done";
    const isActive = stage.state === "active";
    const isFailed = stage.state === "failed";

    if (!isDone && !isActive && !isFailed && !hasFunnel && !pipeline.complete) continue;

    let title = stageTitles[i] ?? `Stage${i}`;
    let kind: PipelineBusinessEvent["kind"] = "stage";
    if (isActive) {
      title = i === 4 ? "Writing Export" : `Stage${i} Running`;
      kind = "running";
    }
    if (i === 4) kind = isActive ? "running" : "export";

    if (!isDone && !isActive && !hasFunnel) continue;

    out.push({
      id: `stage-${i}-${stage.label}`,
      at: estimateStageTimestamp(i, stages, sorted),
      title,
      detail,
      durationSec: stage.duration_sec ?? null,
      kind: isFailed ? "failed" : kind,
    });
  }

  if (failed) {
    out.push({
      id: `failed-${failed.id}`,
      at: failed.created_at,
      title: "Pipeline Failed",
      detail: failed.message ? String(failed.message).slice(0, 80) : null,
      durationSec: null,
      kind: "failed",
    });
  }

  out.sort((a, b) => (a.at ?? 0) - (b.at ?? 0));

  const seen = new Set<string>();
  return out.filter((row) => {
    const key = `${row.title}|${row.detail ?? ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function humanizeJobEvent(ev: StudioStatusResponse["events"][number]): string {
  const to = String(ev.to_status ?? "").trim();
  const msg = String(ev.message ?? "").trim();
  const payload = parseEventPayload(ev.payload_json);
  const stage = String(payload.stage_name ?? "").trim();

  if (stage === "STAGE1_FILTER" || msg.includes("STAGE1")) return "Stage1 Completed";
  if (stage === "STAGE2_FAST_SCORE" || msg.includes("STAGE2")) return "Stage2 Completed";
  if (stage === "STAGE3_VLM" || msg.includes("STAGE3")) return "Stage3 Completed";
  if (stage === "WRITE_ARTIFACT" || msg.includes("WRITE_ARTIFACT")) return "Export Generated";

  switch (to) {
    case "QUEUED":
      return "Job Queued";
    case "CLAIMED":
      return "Worker Claimed";
    case "PREPROCESSING":
      return "Stage1 Running";
    case "INFERENCING":
      return "Stage3 Running";
    case "POSTPROCESSING":
      return "Writing Artifacts";
    case "SUCCEEDED":
      return "Export Generated";
    case "FAILED_RETRYABLE":
    case "FAILED_PERMANENT":
    case "DEAD_LETTERED":
      return "Job Failed";
    case "CANCELLED":
      return "Job Cancelled";
    default:
      if (msg) return msg.length > 48 ? `${msg.slice(0, 45)}…` : msg;
      return to || "Event";
  }
}

export function jobStatusLabel(status: string | undefined, running: boolean): string {
  if (running) return "RUNNING";
  const s = String(status ?? "").trim();
  if (!s) return "IDLE";
  return s;
}
