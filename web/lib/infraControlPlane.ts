/** Shared derivations for the Infra AI Pipeline Control Plane UI. */

export type InfraStageFlowItem = {
  stage_key: string;
  status: string;
  count: number;
  avg_latency_ms?: number | null;
};

export type PipelineNodeId =
  | "ingest"
  | "queue"
  | "stage1"
  | "stage2"
  | "stage3"
  | "export";

export type PipelineNodeStats = {
  id: PipelineNodeId;
  label: string;
  processed: number;
  failed: number;
  active: number;
  avgLatencyMs: number | null;
  flowHint: number;
};

const SUCCEEDED = new Set(["SUCCEEDED"]);
const FAILED = new Set([
  "FAILED_RETRYABLE",
  "FAILED_PERMANENT",
  "DEAD_LETTERED",
  "CANCELLED",
]);
const ACTIVE = new Set(["CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING", "QUEUED"]);

const STAGE_KEY_TO_NODE: Record<string, PipelineNodeId> = {
  ingest: "ingest",
  INGEST: "ingest",
  PREPARE_INPUT: "ingest",
  queue: "queue",
  STAGE1_FILTER: "stage1",
  STAGE1: "stage1",
  STAGE2_FAST_SCORE: "stage2",
  STAGE2: "stage2",
  INFERENCING: "stage3",
  STAGE3: "stage3",
  deep_analysis: "stage3",
  WRITE_ARTIFACT: "export",
  FINALIZE: "export",
  export: "export",
};

function normalizeStageKey(raw: string): PipelineNodeId | null {
  const k = raw.trim();
  if (!k || k === "(legacy)") return null;
  const direct = STAGE_KEY_TO_NODE[k];
  if (direct) return direct;
  const upper = k.toUpperCase();
  if (upper.includes("STAGE1") || upper.includes("FILTER")) return "stage1";
  if (upper.includes("STAGE2") || upper.includes("FAST_SCORE")) return "stage2";
  if (upper.includes("STAGE3") || upper.includes("INFER") || upper.includes("VLM")) return "stage3";
  if (upper.includes("WRITE") || upper.includes("FINAL") || upper.includes("EXPORT")) return "export";
  if (upper.includes("PREPARE") || upper.includes("INGEST")) return "ingest";
  return null;
}

export function buildPipelineTopology(
  stages: InfraStageFlowItem[],
  jobsByStatus: Record<string, number>,
): PipelineNodeStats[] {
  const nodes: Record<PipelineNodeId, PipelineNodeStats> = {
    ingest: { id: "ingest", label: "Ingest", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
    queue: { id: "queue", label: "Queue", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
    stage1: { id: "stage1", label: "Stage 1", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
    stage2: { id: "stage2", label: "Stage 2", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
    stage3: { id: "stage3", label: "Stage 3", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
    export: { id: "export", label: "Export", processed: 0, failed: 0, active: 0, avgLatencyMs: null, flowHint: 0 },
  };

  const latencyAcc: Record<PipelineNodeId, { sum: number; n: number }> = {
    ingest: { sum: 0, n: 0 },
    queue: { sum: 0, n: 0 },
    stage1: { sum: 0, n: 0 },
    stage2: { sum: 0, n: 0 },
    stage3: { sum: 0, n: 0 },
    export: { sum: 0, n: 0 },
  };

  for (const row of stages) {
    const nodeId = normalizeStageKey(row.stage_key);
    if (!nodeId) continue;
    const st = String(row.status ?? "").toUpperCase();
    const c = Number(row.count ?? 0);
    if (SUCCEEDED.has(st)) nodes[nodeId].processed += c;
    else if (FAILED.has(st)) nodes[nodeId].failed += c;
    else if (ACTIVE.has(st)) nodes[nodeId].active += c;
    if (row.avg_latency_ms != null && c > 0) {
      latencyAcc[nodeId].sum += row.avg_latency_ms * c;
      latencyAcc[nodeId].n += c;
    }
  }

  const queued = Number(jobsByStatus.QUEUED ?? 0);
  nodes.queue.active = queued;
  nodes.queue.flowHint = queued;

  for (const id of Object.keys(latencyAcc) as PipelineNodeId[]) {
    const { sum, n } = latencyAcc[id];
    nodes[id].avgLatencyMs = n > 0 ? Math.round(sum / n) : null;
  }

  const order: PipelineNodeId[] = ["ingest", "queue", "stage1", "stage2", "stage3", "export"];
  for (let i = 0; i < order.length; i++) {
    const id = order[i];
    const next = order[i + 1];
    if (!next) break;
    nodes[id].flowHint = Math.max(nodes[id].flowHint, nodes[id].active, nodes[next].active);
  }

  return order.map((id) => nodes[id]);
}

export type FailureBuckets = {
  stage1Failures: number;
  providerTimeouts: number;
  fileReadErrors: number;
  exportFailures: number;
  deadLetterCount: number;
  retryCount: number;
};

function matchErrorBucket(et: string): keyof Omit<FailureBuckets, "deadLetterCount" | "retryCount"> | null {
  const s = et.toLowerCase();
  if (s.includes("timeout") || s.includes("timed_out") || s === "deadline") return "providerTimeouts";
  if (s.includes("file") || s.includes("read") || s.includes("io") || s.includes("enoent")) return "fileReadErrors";
  if (s.includes("export") || s.includes("artifact") || s.includes("write")) return "exportFailures";
  if (s.includes("stage1") || s.includes("filter") || s.includes("opencv")) return "stage1Failures";
  return null;
}

export function aggregateFailureBuckets(
  jobsByStatus: Record<string, number>,
  modelRunsByErrorType: Record<string, number> | undefined,
  stages: InfraStageFlowItem[],
  retryRecentCount: number,
): FailureBuckets {
  let stage1Failures = 0;
  let exportFailures = 0;
  for (const row of stages) {
    const node = normalizeStageKey(row.stage_key);
    if (!FAILED.has(String(row.status).toUpperCase())) continue;
    const c = Number(row.count ?? 0);
    if (node === "stage1") stage1Failures += c;
    if (node === "export") exportFailures += c;
  }

  let providerTimeouts = 0;
  let fileReadErrors = 0;
  for (const [et, c] of Object.entries(modelRunsByErrorType ?? {})) {
    const bucket = matchErrorBucket(et);
    const n = Number(c);
    if (bucket === "providerTimeouts") providerTimeouts += n;
    else if (bucket === "fileReadErrors") fileReadErrors += n;
    else if (bucket === "exportFailures") exportFailures += n;
    else if (bucket === "stage1Failures") stage1Failures += n;
  }

  const deadLetterCount = Number(jobsByStatus.DEAD_LETTERED ?? 0);
  const retryCount = retryRecentCount + Number(jobsByStatus.FAILED_RETRYABLE ?? 0);

  return {
    stage1Failures,
    providerTimeouts,
    fileReadErrors,
    exportFailures,
    deadLetterCount,
    retryCount,
  };
}

/**
 * Stable mock utilization for demo when host metrics are unavailable.
 * @deprecated Do not surface in the observability UI — fabricated metrics
 * erode trust during incidents. Kept only for non-prod demos.
 */
export function mockWorkerUtil(workerKey: string): { cpuPct: number; memPct: number } {
  let h = 0;
  for (let i = 0; i < workerKey.length; i++) h = (h * 31 + workerKey.charCodeAt(i)) >>> 0;
  const cpuPct = 18 + (h % 55);
  const memPct = 22 + ((h >> 8) % 48);
  return { cpuPct, memPct };
}

/** Single client-observed sample of the control plane, accumulated per poll. */
export type InfraHistoryPoint = {
  t: number;
  queued: number;
  running: number;
  failedTotal: number;
  succeededCumulative: number;
  utilPct: number | null;
  pipelineAvgMs: number | null;
  throughputPerMin: number | null;
};

/** Rolling-window throughput from successive cumulative SUCCEEDED counters. */
export function deriveThroughputPerMin(
  prev: InfraHistoryPoint | undefined,
  succeededCumulative: number,
  nowMs: number,
): number | null {
  if (!prev) return null;
  const dt = (nowMs - prev.t) / 1000;
  if (dt <= 0) return null;
  const delta = succeededCumulative - prev.succeededCumulative;
  if (delta < 0) return null; // counter reset / restart
  return Math.round((delta / dt) * 60 * 10) / 10;
}

/** Session error rate (0–100) from completed jobs in the current snapshot. */
export function errorRatePct(succeeded: number, failedTotal: number): number | null {
  const done = succeeded + failedTotal;
  if (done <= 0) return null;
  return Math.round((failedTotal / done) * 1000) / 10;
}

export type InfraHealthVerdict = "operational" | "degraded" | "down";

/** Single-line system verdict for the sticky status bar. */
export function deriveSystemHealth(input: {
  celeryUnavailable: boolean;
  onlineWorkers: number;
  admissionHeadroom: number | undefined;
  utilPct: number | null;
  failedJobs: number;
  unmatchedWorkers: number;
}): { verdict: InfraHealthVerdict; reason: string } {
  if (input.celeryUnavailable) return { verdict: "down", reason: "Broker unreachable — dispatch paused" };
  if (input.onlineWorkers <= 0) return { verdict: "down", reason: "No online workers" };
  if ((input.admissionHeadroom ?? 1) <= 0)
    return { verdict: "degraded", reason: "Admission closed — inflight at capacity" };
  if ((input.utilPct ?? 0) >= 100) return { verdict: "degraded", reason: "Pool saturated (100%)" };
  if (input.unmatchedWorkers > 0)
    return { verdict: "degraded", reason: `${input.unmatchedWorkers} broker worker(s) unmatched` };
  if (input.failedJobs > 0) return { verdict: "degraded", reason: `${input.failedJobs} job(s) in a failed state` };
  return { verdict: "operational", reason: "All systems nominal" };
}

/** Relative "x ago" label for a unix-ms timestamp. */
export function freshnessLabel(lastMs: number | null, nowMs: number): string {
  if (!lastMs) return "never";
  const d = Math.max(0, Math.floor((nowMs - lastMs) / 1000));
  if (d < 2) return "just now";
  if (d < 60) return `${d}s ago`;
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  return `${Math.floor(d / 3600)}h ago`;
}

export function admissionOpen(headroom: number | undefined, celeryDown: boolean): {
  open: boolean;
  label: string;
  shortLabel: string;
  tone: "ok" | "warn" | "down";
} {
  if (celeryDown) {
    return {
      open: false,
      label: "Broker unavailable — dispatch paused",
      shortLabel: "Admission Closed ✗",
      tone: "down",
    };
  }
  const h = headroom ?? 0;
  if (h <= 0) {
    return {
      open: false,
      label: "Inflight at capacity — new claims blocked",
      shortLabel: "Admission Closed ✗",
      tone: "warn",
    };
  }
  if (h < 3) {
    return {
      open: true,
      label: "Accepting jobs · tight headroom",
      shortLabel: "Accepting New Jobs ✓",
      tone: "warn",
    };
  }
  return {
    open: true,
    label: "Scheduler accepting new work",
    shortLabel: "Accepting New Jobs ✓",
    tone: "ok",
  };
}

/** Pipeline slot utilization from admission snapshot (0–100). */
export function pipelineUtilizationPct(
  totalInflight: number | undefined,
  totalCapacity: number | undefined,
): number | null {
  const cap = totalCapacity ?? 0;
  if (cap <= 0) return totalInflight != null && totalInflight > 0 ? 100 : null;
  const inf = Math.max(0, totalInflight ?? 0);
  return Math.min(100, Math.round((inf / cap) * 100));
}

export function pipelineHeadroomLabel(
  headroom: number | undefined,
  totalCapacity: number | undefined,
): string {
  const h = headroom ?? 0;
  const cap = totalCapacity ?? 0;
  if (cap > 0) return `Pipeline Admission Headroom: ${h}/${cap} available`;
  if (headroom != null) return `Pipeline Admission Headroom: ${h} slots`;
  return "Pipeline Admission Headroom: —";
}

export function formatLatencyMs(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return "—";
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}
