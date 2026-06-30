"use client";

type BrokerTotals = {
  active?: number;
  reserved?: number;
  scheduled?: number;
  redis_list_len?: number | null;
  celery_unavailable?: boolean;
  redis_error?: string | null;
  workers?: number;
};

type Props = {
  jobsTotal: number;
  jobsQueued: number;
  jobsActivePipeline: number;
  broker: BrokerTotals | null;
  inferenceQueue: {
    depth?: number;
    active_workers?: number;
    max_inflight?: number;
    avg_job_e2e_ms?: number | null;
  } | null;
  modelRunsInflightDb: number | null;
  workerFresh: number;
  workerTotal: number;
  heartbeatWindowSec: number;
  pipelineAdmission: {
    headroom?: number;
    total_capacity?: number;
    total_inflight?: number;
    online_workers?: number;
    total_worker_rows?: number;
  } | null;
  effectiveHeadroomPools: Record<string, number> | null;
  fallbackCount: number;
  deadLetterCount: number;
  failedRetryableCount: number;
  failedPermanentCount: number;
};

function MetricCard({
  label,
  value,
  hint,
  emphasize,
}: {
  label: string;
  value: string | number;
  hint?: string;
  emphasize?: "warn" | "muted";
}) {
  const tone =
    emphasize === "warn"
      ? "border-amber-600/40 bg-amber-950/20"
      : emphasize === "muted"
        ? "border-stroke/80 bg-panel2/50"
        : "border-stroke bg-panel2";
  return (
    <div className={`rounded-xl border p-4 ${tone}`}>
      <div className="text-xs uppercase tracking-wide text-zinc-500">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-zinc-100">{value}</div>
      {hint ? <div className="mt-1 whitespace-pre-line text-xs leading-snug text-zinc-500">{hint}</div> : null}
    </div>
  );
}

export function InfraSummary({
  jobsTotal,
  jobsQueued,
  jobsActivePipeline,
  broker,
  inferenceQueue,
  modelRunsInflightDb,
  workerFresh,
  workerTotal,
  heartbeatWindowSec,
  pipelineAdmission,
  effectiveHeadroomPools,
  fallbackCount,
  deadLetterCount,
  failedRetryableCount,
  failedPermanentCount,
}: Props) {
  const redisLen = broker?.redis_list_len;
  const brokerLine =
    broker?.celery_unavailable === true
      ? "inspect 不可用（见 metrics_authority · broker_best_effort）"
      : [
          broker?.active != null ? `active ${broker.active}` : null,
          broker?.reserved != null ? `reserved ${broker.reserved}` : null,
          broker?.scheduled != null ? `scheduled ${broker.scheduled}` : null,
          redisLen != null ? `redis celery ${redisLen}` : null,
          broker?.workers != null ? `workers ${broker.workers}` : null,
        ]
          .filter(Boolean)
          .join(" · ") || "—";

  const brokerHint =
    broker?.redis_error && !broker?.celery_unavailable
      ? `${brokerLine}\nRedis llen: ${broker.redis_error}`
      : brokerLine;

  const infQ = inferenceQueue;
  const infHeadroom =
    infQ?.max_inflight != null && infQ.max_inflight > 0
      ? Math.max(0, (infQ.max_inflight ?? 0) - (infQ.depth ?? 0) - (infQ.active_workers ?? 0))
      : null;
  const infHint = [
    infQ != null
      ? `进程内 VLM 队列: depth ${infQ.depth ?? "—"} · active ${infQ.active_workers ?? "—"} / max ${infQ.max_inflight ?? "—"}`
      : null,
    infHeadroom != null ? `粗算 headroom ≈ ${infHeadroom}（admission 槽位，非 Celery）` : null,
    modelRunsInflightDb != null ? `DB 中推理行 inflight（QUEUED/STARTED）: ${modelRunsInflightDb}` : null,
  ]
    .filter(Boolean)
    .join("\n");

  const adm = pipelineAdmission;
  const admValue =
    adm != null
      ? `${adm.headroom ?? "—"} / ${adm.total_capacity ?? "—"}`
      : "—";
  const admHint = [
    adm != null ? `ONLINE workers: ${adm.online_workers ?? "—"} · rows: ${adm.total_worker_rows ?? "—"}` : null,
    adm != null ? `live inflight on workers: ${adm.total_inflight ?? "—"}` : null,
    effectiveHeadroomPools && Object.keys(effectiveHeadroomPools).length
      ? `按池 effective: ${Object.entries(effectiveHeadroomPools)
          .map(([k, v]) => `${k}=${v}`)
          .join(", ")}`
      : null,
  ]
    .filter(Boolean)
    .join("\n");

  const failHint = [
    `DEAD_LETTERED: ${deadLetterCount}（需手动 retry）`,
    `FAILED_RETRYABLE: ${failedRetryableCount}`,
    `FAILED_PERMANENT: ${failedPermanentCount}`,
  ].join("\n");

  return (
    <section className="space-y-3">
      <p className="text-xs text-zinc-600">
        指标来源见 API <code className="text-zinc-500">metrics_authority</code>：SQLite 为 job/worker SSOT；Celery inspect / Redis llen 为 best-effort；provider
        计数与 inference_queue 多为<strong className="font-normal text-zinc-500">本 API 进程内</strong>样本。
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Jobs · 队列 / 运行"
          value={`${jobsQueued} / ${jobsActivePipeline}`}
          hint={`SSOT 总计 ${jobsTotal}\nQUEUED vs 管线中（CLAIMED…POSTPROCESSING）`}
        />
        <MetricCard
          label="Celery broker 压力"
          value={broker?.celery_unavailable ? "n/a" : redisLen ?? "—"}
          hint={brokerHint}
          emphasize={broker?.celery_unavailable ? "warn" : undefined}
        />
        <MetricCard
          label="Pipeline admission headroom"
          value={admValue}
          hint={admHint || "capacity − live inflight（仅 ONLINE workers）"}
        />
        <MetricCard
          label="Workers heartbeat"
          value={`${workerFresh} / ${workerTotal}`}
          hint={`${heartbeatWindowSec}s 内有心跳的 worker 数 / workers 表行数`}
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <MetricCard label="Inference Q（本进程）" value={infQ?.depth ?? "—"} hint={infHint || "PrioritizedInferenceQueue 快照"} />
        <MetricCard
          label="Provider fallbacks（本进程）"
          value={fallbackCount}
          hint="按 provider 聚合的 fallback 次数；跨 worker 请看 model_runs.fallback_used"
        />
        <MetricCard
          label="失败 / 重试 分层"
          value={deadLetterCount + failedRetryableCount + failedPermanentCount}
          hint={`${failHint}\n下方 dead-letter 表可一键 POST retry`}
          emphasize={deadLetterCount > 0 ? "warn" : "muted"}
        />
      </div>
    </section>
  );
}
