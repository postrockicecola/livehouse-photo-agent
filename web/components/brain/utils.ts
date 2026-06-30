import {
  PIPELINE_ACTIVE,
  PIPELINE_FAILED,
  PIPELINE_STAGES,
  type InfraMetricsSnapshot,
  type RuntimeHealth,
  type StageFlowStat,
} from "./types";

export function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec <= 0) return "—";
  const d = new Date(sec * 1000);
  return d.toLocaleTimeString("en-GB", { hour12: false });
}

export function fmtLatency(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function fmtPct(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${n.toFixed(1)}%`;
}

export function shortPath(p: unknown, max = 48): string {
  const s = String(p ?? "");
  if (s.length <= max) return s;
  return `…${s.slice(-max + 1)}`;
}

export function computeHealth(metrics: InfraMetricsSnapshot | null): RuntimeHealth {
  if (!metrics) return "UNKNOWN";
  const fresh = Number(metrics.workers?.fresh_within_120s ?? 0);
  const brokerDown = metrics.queue_backlog?.celery_unavailable === true;
  const failed =
    Number(metrics.jobs?.by_status?.FAILED_RETRYABLE ?? 0) +
    Number(metrics.jobs?.by_status?.DEAD_LETTERED ?? 0) +
    Number(metrics.jobs?.by_status?.FAILED_PERMANENT ?? 0);
  if (fresh === 0) return "CRITICAL";
  if (brokerDown || failed > 5) return "DEGRADED";
  return "HEALTHY";
}

export function computeSuccessRate(metrics: InfraMetricsSnapshot | null): number | null {
  const by = metrics?.jobs?.by_status ?? {};
  const ok = Number(by.SUCCEEDED ?? 0);
  const fail =
    Number(by.FAILED_PERMANENT ?? 0) +
    Number(by.FAILED_RETRYABLE ?? 0) +
    Number(by.DEAD_LETTERED ?? 0) +
    Number(by.CANCELLED ?? 0);
  const total = ok + fail;
  if (total === 0) return null;
  return (ok / total) * 100;
}

export function computeQueueBacklog(metrics: InfraMetricsSnapshot | null): number {
  const by = metrics?.jobs?.by_status ?? {};
  const queued = Number(by.QUEUED ?? 0);
  const broker = metrics?.queue_backlog;
  const celery = (broker?.active ?? 0) + (broker?.reserved ?? 0) + (broker?.scheduled ?? 0);
  const vlm = Number(metrics?.inference_queue?.depth ?? 0);
  return Math.max(queued, celery, vlm);
}

export function computeInflight(metrics: InfraMetricsSnapshot | null): number {
  const by = metrics?.jobs?.by_status ?? {};
  let n = 0;
  for (const [st, c] of Object.entries(by)) {
    if (PIPELINE_ACTIVE.has(st)) n += Number(c ?? 0);
  }
  const dbInf = metrics?.inference_from_database?.model_runs_inflight_in_db;
  if (dbInf != null) return Math.max(n, dbInf);
  return n;
}

export function computeP95Latency(metrics: InfraMetricsSnapshot | null): number | null {
  const e2e = metrics?.inference_queue?.avg_job_e2e_ms;
  if (e2e != null) return e2e;
  const avg = metrics?.inference_latency?.avg_ms;
  if (avg != null) return avg;
  const providers = metrics?.inference_from_database?.by_provider ?? [];
  const lats = providers.map((p) => p.avg_provider_latency_ms).filter((x): x is number => x != null);
  if (!lats.length) return null;
  return Math.round(lats.reduce((a, b) => a + b, 0) / lats.length);
}

export type StageRollup = {
  key: string;
  label: string;
  running: number;
  queued: number;
  failed: number;
  avgLatencyMs: number | null;
};

export function rollupStageStats(stages: StageFlowStat[]): StageRollup[] {
  const byKey = new Map<string, StageRollup>();

  for (const def of PIPELINE_STAGES) {
    byKey.set(def.key, {
      key: def.key,
      label: def.label,
      running: 0,
      queued: 0,
      failed: 0,
      avgLatencyMs: null,
    });
  }

  const latencyAcc = new Map<string, { sum: number; n: number }>();

  for (const row of stages) {
    const key = row.stage_key;
    let rollup = byKey.get(key);
    if (!rollup) {
      rollup = {
        key,
        label: key.replace(/_/g, " ").slice(0, 12),
        running: 0,
        queued: 0,
        failed: 0,
        avgLatencyMs: null,
      };
      byKey.set(key, rollup);
    }
    const st = row.status;
    const c = row.count;
    if (PIPELINE_ACTIVE.has(st)) rollup.running += c;
    else if (st === "QUEUED") rollup.queued += c;
    else if (PIPELINE_FAILED.has(st)) rollup.failed += c;

    if (row.avg_latency_ms != null) {
      const acc = latencyAcc.get(key) ?? { sum: 0, n: 0 };
      acc.sum += row.avg_latency_ms * c;
      acc.n += c;
      latencyAcc.set(key, acc);
    }
  }

  for (const [key, acc] of latencyAcc) {
    const r = byKey.get(key);
    if (r && acc.n > 0) r.avgLatencyMs = Math.round(acc.sum / acc.n);
  }

  const ordered = PIPELINE_STAGES.map((s) => byKey.get(s.key)).filter((x): x is StageRollup => x != null);
  const extras = [...byKey.values()].filter((x) => !PIPELINE_STAGES.some((s) => s.key === x.key));
  return [...ordered, ...extras];
}

export function formatEventLine(ev: {
  created_at: number;
  message?: string | null;
  from_status?: string | null;
  to_status?: string | null;
  job_id: number;
  worker_name?: string | null;
  stage_name?: string | null;
}): string {
  const msg = (ev.message ?? "").trim();
  if (msg) return msg;
  const wn = ev.worker_name ? `worker ${ev.worker_name}` : null;
  const stage = ev.stage_name ? `stage ${ev.stage_name}` : null;
  const trans = [ev.from_status, ev.to_status].filter(Boolean).join(" → ");
  const parts = [
    wn ? `${wn} claimed job #${ev.job_id}` : `job #${ev.job_id}`,
    trans || null,
    stage,
  ].filter(Boolean);
  return parts.join(" · ");
}
