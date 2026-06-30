/** Map ``job_events`` rows to operator-facing pipeline timeline labels. */

export type InfraRuntimeEventRow = {
  id: number;
  job_id: number;
  from_status?: string | null;
  to_status?: string | null;
  created_at: number;
  message?: string | null;
  stage_name?: string | null;
  worker_id?: number | null;
  worker_name?: string | null;
  trace_id?: string | null;
};

export type PipelineTimelineTone = "normal" | "running" | "success" | "failure" | "retry";

export type PipelineTimelineEntry = {
  id: number;
  created_at: number;
  job_id: number;
  trace_id: string | null;
  label: string;
  tone: PipelineTimelineTone;
  detail?: string;
};

const RUNNING_TO = new Set(["CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"]);

function stageBucket(stageName: string | null | undefined): "s1" | "s2" | "s3" | "export" | "other" {
  const sn = (stageName ?? "").toUpperCase();
  if (sn.includes("STAGE1") || sn.includes("FILTER")) return "s1";
  if (sn.includes("STAGE2") || sn.includes("FAST_SCORE")) return "s2";
  if (sn.includes("STAGE3") || sn.includes("VLM") || sn.includes("INFER") || sn.includes("DEEP")) return "s3";
  if (sn.includes("WRITE") || sn.includes("FINAL") || sn.includes("ARTIFACT")) return "export";
  return "other";
}

function isRetryEvent(ev: InfraRuntimeEventRow): boolean {
  const msg = (ev.message ?? "").toLowerCase();
  const from = (ev.from_status ?? "").toUpperCase();
  const to = (ev.to_status ?? "").toUpperCase();
  if (msg.includes("retry")) return true;
  if (from === "FAILED_RETRYABLE" && to === "QUEUED") return true;
  if (from === "DEAD_LETTERED" && to === "QUEUED") return true;
  return false;
}

export function pipelineTimelineLabel(ev: InfraRuntimeEventRow): { label: string; tone: PipelineTimelineTone; detail?: string } {
  const msg = (ev.message ?? "").trim();
  const msgL = msg.toLowerCase();
  const from = (ev.from_status ?? "").toUpperCase();
  const to = (ev.to_status ?? "").toUpperCase();
  const stage = stageBucket(ev.stage_name);
  const wn = ev.worker_name?.trim();

  if (isRetryEvent(ev)) {
    return { label: "Retry triggered", tone: "retry", detail: msg || `${from} → ${to}` };
  }
  if (to === "DEAD_LETTERED") {
    return { label: "Dead-lettered", tone: "failure", detail: msg || undefined };
  }
  if (to === "FAILED_PERMANENT" || to === "FAILED_RETRYABLE") {
    return { label: "Job failed", tone: "failure", detail: msg || to };
  }
  if (to === "SUCCEEDED") {
    if (stage === "export" || msgL.includes("artifact") || msgL.includes("export") || msgL.includes("persist")) {
      return { label: "Export generated", tone: "success", detail: msg || ev.stage_name || undefined };
    }
    if (stage === "s3" || msgL.includes("vlm") || msgL.includes("inference")) {
      return { label: "Stage3 completed", tone: "success", detail: msg || undefined };
    }
    if (stage === "s2") {
      return { label: "Stage2 completed", tone: "success", detail: msg || undefined };
    }
    if (stage === "s1" || msgL.includes("filter") || msgL.includes("preprocess")) {
      return { label: "Stage1 completed", tone: "success", detail: msg || undefined };
    }
    if (msgL.includes("postprocess complete")) {
      const st = (ev.stage_name ?? "").toUpperCase();
      if (st.includes("STAGE2")) return { label: "Stage2 completed", tone: "success", detail: msg };
      if (st.includes("STAGE3")) return { label: "Stage3 completed", tone: "success", detail: msg };
      if (st.includes("STAGE1")) return { label: "Stage1 completed", tone: "success", detail: msg };
    }
    return { label: "Job succeeded", tone: "success", detail: msg || undefined };
  }
  if (to === "CLAIMED" || (from === "QUEUED" && to === "CLAIMED")) {
    return {
      label: "Worker claimed",
      tone: "running",
      detail: wn ? `${wn} · job #${ev.job_id}` : msg || undefined,
    };
  }
  if (to === "QUEUED" && (!from || from === "QUEUED" || from === "NULL")) {
    return { label: "Job queued", tone: "normal", detail: msg || undefined };
  }
  if (to === "PREPROCESSING" || msgL.includes("preprocessing")) {
    return { label: "Stage1 started", tone: "running", detail: msg || wn || undefined };
  }
  if (to === "INFERENCING" || msgL.includes("inferencing") || msgL.includes("aesthetic pipeline")) {
    return { label: "Stage3 started", tone: "running", detail: msg || undefined };
  }
  if (to === "POSTPROCESSING") {
    if (stage === "s2") return { label: "Stage2 started", tone: "running", detail: msg || undefined };
    if (stage === "s3") return { label: "Stage3 started", tone: "running", detail: msg || undefined };
    return { label: "Stage processing", tone: "running", detail: msg || undefined };
  }
  if (RUNNING_TO.has(to)) {
    return { label: msg || `${from || "∅"} → ${to}`, tone: "running" };
  }
  if (msg) return { label: msg, tone: "normal" };
  const trans = [ev.from_status, ev.to_status].filter(Boolean).join(" → ");
  return { label: trans || "Pipeline event", tone: "normal" };
}

export function buildPipelineTimelineEntries(
  events: InfraRuntimeEventRow[],
  limit = 20,
): PipelineTimelineEntry[] {
  const slice = events.length > limit ? events.slice(-limit) : events;
  const mapped = slice.map((ev) => {
    const { label, tone, detail } = pipelineTimelineLabel(ev);
    return {
      id: ev.id,
      created_at: ev.created_at,
      job_id: ev.job_id,
      trace_id: ev.trace_id?.trim() || null,
      label,
      tone,
      detail,
    };
  });
  return mapped.sort((a, b) => b.created_at - a.created_at || b.id - a.id);
}

export const TIMELINE_TONE_CLASS: Record<PipelineTimelineTone, string> = {
  normal: "text-zinc-300",
  running: "text-emerald-300",
  success: "text-emerald-400",
  failure: "text-red-300",
  retry: "text-amber-300",
};

export const TIMELINE_TONE_DOT: Record<PipelineTimelineTone, string> = {
  normal: "bg-zinc-500",
  running: "bg-emerald-400",
  success: "bg-emerald-400",
  failure: "bg-red-400",
  retry: "bg-amber-400",
};

export function formatTimelineTs(sec: number): string {
  if (!Number.isFinite(sec) || sec <= 0) return "—";
  try {
    return new Date(sec * 1000).toLocaleTimeString(undefined, { hour12: false });
  } catch {
    return "—";
  }
}

export function shortTraceId(trace: string | null): string {
  if (!trace) return "—";
  if (trace.length <= 14) return trace;
  return `${trace.slice(0, 6)}…${trace.slice(-4)}`;
}
