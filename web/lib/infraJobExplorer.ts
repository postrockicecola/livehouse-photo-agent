import { formatLatencyMs } from "@/lib/infraControlPlane";

export type InfraJobRow = {
  id?: number;
  job_type?: string;
  status?: string;
  worker_id?: number | null;
  session_id?: number | null;
  attempt?: number | null;
  max_attempts?: number | null;
  updated_at?: number | null;
  created_at?: number | null;
  trace_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  queue_wait_ms?: number | null;
  total_latency_ms?: number | null;
  preprocess_ms?: number | null;
  inference_ms?: number | null;
  postprocess_ms?: number | null;
  stage_name?: string | null;
  enqueued_at?: number | null;
  claimed_at?: number | null;
  started_at?: number | null;
  finished_at?: number | null;
  root_job_id?: number | null;
  stage_order?: number | null;
  is_stage?: number | boolean | null;
};

export const RUNNING_STATUSES = ["CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"] as const;
export const FAILED_STATUSES = ["FAILED_RETRYABLE", "FAILED_PERMANENT"] as const;

export type StatusFilterKey = "ALL" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "DEAD_LETTERED";

export const STATUS_FILTER_OPTIONS: { key: StatusFilterKey; label: string; apiStatuses?: string[] }[] = [
  { key: "ALL", label: "All" },
  { key: "QUEUED", label: "Queued", apiStatuses: ["QUEUED"] },
  { key: "RUNNING", label: "Running", apiStatuses: [...RUNNING_STATUSES] },
  { key: "SUCCEEDED", label: "Succeeded", apiStatuses: ["SUCCEEDED"] },
  { key: "FAILED", label: "Failed", apiStatuses: [...FAILED_STATUSES] },
  { key: "DEAD_LETTERED", label: "Dead letter", apiStatuses: ["DEAD_LETTERED"] },
];

export function formatMs(ms: number | null | undefined): string {
  if (ms == null || ms < 0) return "—";
  return formatLatencyMs(ms);
}

export function runDurationMs(job: InfraJobRow): number | null {
  if (job.total_latency_ms != null && job.total_latency_ms > 0) return job.total_latency_ms;
  const start = job.started_at ?? job.claimed_at;
  const end = job.finished_at ?? job.updated_at;
  if (start != null && end != null && end >= start) return (end - start) * 1000;
  return null;
}

function stageLatencyMs(row: InfraJobRow): number | null {
  const sn = (row.stage_name ?? "").toUpperCase();
  if (sn.includes("STAGE3") || sn.includes("VLM") || sn.includes("INFER") || sn.includes("DEEP")) {
    return row.inference_ms ?? row.total_latency_ms ?? runDurationMs(row);
  }
  if (sn.includes("STAGE1") || sn.includes("FILTER")) {
    return row.preprocess_ms ?? row.total_latency_ms ?? runDurationMs(row);
  }
  if (sn.includes("STAGE2") || sn.includes("FAST_SCORE")) {
    return row.total_latency_ms ?? runDurationMs(row);
  }
  return row.total_latency_ms ?? runDurationMs(row);
}

/** Root id for a ``PIPELINE_STAGE`` group (shared ``root_job_id``). */
export function stageGroupRootId(job: InfraJobRow): number | null {
  if (job.root_job_id != null) return Number(job.root_job_id);
  if ((job.job_type ?? "") === "PIPELINE_STAGE" && job.id != null) return Number(job.id);
  return null;
}

/** Aggregate S1 / S2 / S3 from all rows in a stage group. */
export function stageGroupBreakdownLabel(rows: InfraJobRow[]): string {
  let s1: number | null = null;
  let s2: number | null = null;
  let s3: number | null = null;
  for (const row of rows) {
    const sn = (row.stage_name ?? "").toUpperCase();
    const ms = stageLatencyMs(row);
    if (sn.includes("STAGE1") || sn.includes("FILTER")) s1 = ms;
    else if (sn.includes("STAGE2") || sn.includes("FAST_SCORE")) s2 = ms;
    else if (sn.includes("STAGE3") || sn.includes("VLM") || sn.includes("INFER") || sn.includes("DEEP")) s3 = ms;
  }
  const parts: string[] = [];
  if (s1 != null) parts.push(`S1 ${formatMs(s1)}`);
  if (s2 != null) parts.push(`S2 ${formatMs(s2)}`);
  if (s3 != null) parts.push(`S3 ${formatMs(s3)}`);
  if (parts.length) return parts.join(" · ");
  return stageBreakdownLabel(rows[0] ?? {});
}

/** S1/S2/S3 from job row (monolithic columns or per-stage ``total_latency_ms``). */
export function stageBreakdownLabel(job: InfraJobRow): string {
  const sn = (job.stage_name ?? "").toUpperCase();
  if (sn.includes("STAGE1") || sn.includes("FILTER")) {
    return `S1 ${formatMs(stageLatencyMs(job))}`;
  }
  if (sn.includes("STAGE2") || sn.includes("FAST_SCORE")) {
    return `S2 ${formatMs(stageLatencyMs(job))}`;
  }
  if (sn.includes("STAGE3") || sn.includes("VLM") || sn.includes("INFER") || sn.includes("DEEP")) {
    return `S3 ${formatMs(stageLatencyMs(job))}`;
  }
  if (sn.includes("WRITE") || sn.includes("FINAL")) {
    return `Export ${formatMs(job.total_latency_ms)}`;
  }
  if (job.preprocess_ms != null || job.inference_ms != null || job.postprocess_ms != null) {
    const parts = [`S1 ${formatMs(job.preprocess_ms)}`, `S3 ${formatMs(job.inference_ms)}`];
    if (job.postprocess_ms != null && job.postprocess_ms > 0) {
      parts.splice(1, 0, `S2 ${formatMs(job.postprocess_ms)}`);
    }
    return parts.join(" · ");
  }
  return formatMs(job.total_latency_ms);
}

/** List-view retry chain (``attempt1 failed → attempt2 success``) from attempt counter + status. */
export function retryHistoryShortFromJob(job: InfraJobRow): string {
  const att = Number(job.attempt ?? 0);
  const st = (job.status ?? "").toUpperCase();
  if (st === "SUCCEEDED") {
    if (att <= 0) return "attempt1 success";
    const parts: string[] = [];
    for (let i = 1; i <= att; i++) parts.push(`attempt${i} failed`);
    parts.push(`attempt${att + 1} success`);
    return parts.join(" → ");
  }
  if (st === "FAILED_RETRYABLE" || st === "FAILED_PERMANENT" || st === "DEAD_LETTERED") {
    const cur = att + 1;
    const parts: string[] = [];
    for (let i = 1; i < cur; i++) parts.push(`attempt${i} failed`);
    parts.push(`attempt${cur} failed`);
    if (st === "DEAD_LETTERED") parts.push("exhausted");
    return parts.join(" → ");
  }
  if (st === "QUEUED" && att > 0) {
    const parts: string[] = [];
    for (let i = 1; i <= att; i++) parts.push(`attempt${i} failed`);
    parts.push(`attempt${att + 1} queued`);
    return parts.join(" → ");
  }
  if (isRunningJob(st) && att > 0) {
    const parts: string[] = [];
    for (let i = 1; i <= att; i++) parts.push(`attempt${i} failed`);
    parts.push(`attempt${att + 1} running`);
    return parts.join(" → ");
  }
  if (att > 0) return retrySummaryShort(job);
  return "—";
}

export function retrySummaryShort(job: InfraJobRow): string {
  const att = Number(job.attempt ?? 0);
  const st = (job.status ?? "").toUpperCase();
  const base = attemptLabel(job);
  if (st === "SUCCEEDED" && att > 0) return `${base} · recovered`;
  if (st === "FAILED_RETRYABLE") return `${base} · retryable`;
  if (st === "DEAD_LETTERED") return `${base} · exhausted`;
  if (att > 0 && st === "QUEUED") return `${base} · re-queued`;
  return base;
}

export function attemptLabel(job: InfraJobRow): string {
  const cur = Number(job.attempt ?? 0) + 1;
  const max = job.max_attempts ?? "—";
  return `${cur}/${max}`;
}

export function isRunningJob(status?: string | null): boolean {
  return RUNNING_STATUSES.includes((status ?? "") as (typeof RUNNING_STATUSES)[number]);
}

export function isFailedJob(status?: string | null): boolean {
  const s = status ?? "";
  return FAILED_STATUSES.includes(s as (typeof FAILED_STATUSES)[number]) || s === "DEAD_LETTERED";
}

export function isRetryHighlighted(job: InfraJobRow): boolean {
  if (isRunningJob(job.status)) return false;
  const att = Number(job.attempt ?? 0);
  const st = job.status ?? "";
  return att > 0 || st === "FAILED_RETRYABLE" || st === "DEAD_LETTERED";
}

export type JobEventRow = {
  id?: number;
  from_status?: string | null;
  to_status?: string | null;
  created_at?: number;
  message?: string | null;
  payload_json?: string | null;
};

export function retryHistoryFromEvents(events: JobEventRow[]): string {
  const attempts: string[] = [];
  let n = 0;
  for (const ev of events) {
    const to = (ev.to_status ?? "").toUpperCase();
    const from = (ev.from_status ?? "").toUpperCase();
    if (to === "CLAIMED" || (from === "QUEUED" && to === "PREPROCESSING")) {
      n += 1;
    }
    if (to.startsWith("FAILED") || to === "DEAD_LETTERED") {
      attempts.push(`attempt${n || attempts.length + 1} failed`);
    }
    if (to === "SUCCEEDED") {
      attempts.push(`attempt${n || attempts.length + 1} success`);
    }
  }
  if (attempts.length === 0) return "—";
  return attempts.join(" → ");
}

export function workerMigrationsFromEvents(events: JobEventRow[]): Array<{ at: number; workerId: number | null; note: string }> {
  const out: Array<{ at: number; workerId: number | null; note: string }> = [];
  let last: number | null = null;
  for (const ev of events) {
    if (!ev.payload_json) continue;
    try {
      const p = JSON.parse(ev.payload_json) as { worker_id?: number };
      const wid = p.worker_id != null ? Number(p.worker_id) : null;
      if (wid != null && wid !== last) {
        out.push({
          at: ev.created_at ?? 0,
          workerId: wid,
          note: ev.message ?? `${ev.from_status ?? "?"} → ${ev.to_status ?? "?"}`,
        });
        last = wid;
      }
    } catch {
      /* ignore */
    }
  }
  return out;
}

export function buildJobsQuery(
  apiBase: string,
  opts: {
    statusFilter: StatusFilterKey;
    traceQuery: string;
    limit?: number;
  },
): string {
  const params = new URLSearchParams();
  params.set("limit", String(opts.limit ?? 50));
  params.set("offset", "0");
  const def = STATUS_FILTER_OPTIONS.find((o) => o.key === opts.statusFilter);
  if (def?.apiStatuses?.length) {
    for (const s of def.apiStatuses) params.append("status", s);
  }
  const tq = opts.traceQuery.trim();
  if (tq) params.set("trace_id", tq);
  return `${apiBase}/api/infra/jobs?${params.toString()}`;
}

export function clientJobMatchesSearch(
  job: InfraJobRow,
  q: string,
  workerNameById: Record<number, string>,
): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  if (String(job.id ?? "").includes(needle)) return true;
  if ((job.trace_id ?? "").toLowerCase().includes(needle)) return true;
  if (String(job.session_id ?? "").includes(needle)) return true;
  if (String(job.worker_id ?? "").includes(needle)) return true;
  const wn = job.worker_id != null ? workerNameById[job.worker_id] : "";
  if (wn && wn.toLowerCase().includes(needle)) return true;
  return false;
}
