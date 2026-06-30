"use client";

export type CeleryBrokerStatus = {
  online?: boolean;
  celery_hostname?: string;
  active_count?: number;
  reserved_count?: number;
  scheduled_count?: number;
  active_tasks?: Array<{ name?: string; id?: string; args_preview?: string | null }>;
  pool_max_concurrency?: number | null;
};

export type InfraWorkerRow = {
  id?: number;
  worker_name?: string;
  worker_type?: string;
  status?: string;
  capacity?: number | null;
  inflight?: number | null;
  last_heartbeat?: number | null;
  celery_broker?: CeleryBrokerStatus;
};

type ProviderRuntime = {
  requests?: number;
  failures?: number;
  fallbacks?: number;
  avg_latency_ms?: number | null;
  last_latency_ms?: number | null;
};

type ProviderItem = {
  name: string;
  display_name?: string;
  enabled: boolean;
  endpoint?: string | null;
  model_name?: string | null;
  fallback_model_name?: string | null;
  runtime?: ProviderRuntime | Record<string, unknown> | null;
};

type Admission = {
  headroom?: number;
  total_capacity?: number;
  total_inflight?: number;
  online_workers?: number;
  total_worker_rows?: number;
};

type ExecutorPools = {
  wildcard_headroom?: number;
  effective_headroom_by_required_pool?: Record<string, number>;
};

type BrokerSummary = {
  celery_unavailable?: boolean;
  worker_count?: number;
  error?: string | null;
};

export type UnmatchedBrokerWorker = {
  celery_hostname?: string;
  active_count?: number;
  reserved_count?: number;
  active_tasks?: Array<{ name?: string; args_preview?: string | null }>;
};

type Props = {
  workers: InfraWorkerRow[];
  providers: ProviderItem[];
  activeProvider: string;
  loading: boolean;
  admission: Admission | null;
  executorPools: ExecutorPools | null;
  brokerSummary?: BrokerSummary | null;
  unmatchedBrokerWorkers?: UnmatchedBrokerWorker[];
};

const HEARTBEAT_FRESH_SEC = 120;

function formatHeartbeat(ts?: number | null): { text: string; stale: boolean } {
  if (!ts) return { text: "—", stale: true };
  const delta = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  let text: string;
  if (delta < 60) text = `${delta}s ago`;
  else if (delta < 3600) text = `${Math.floor(delta / 60)}m ago`;
  else text = `${Math.floor(delta / 3600)}h ago`;
  return { text, stale: delta > HEARTBEAT_FRESH_SEC };
}

function poolChips(pools: ExecutorPools | null): string {
  if (!pools?.effective_headroom_by_required_pool) return "";
  return Object.entries(pools.effective_headroom_by_required_pool)
    .map(([k, v]) => `${k}:${v}`)
    .join(" · ");
}

function brokerBadge(w: InfraWorkerRow): { label: string; cls: string } {
  const cb = w.celery_broker;
  if (cb?.online) {
    const busy = (cb.active_count ?? 0) > 0 || (cb.reserved_count ?? 0) > 0;
    return {
      label: busy ? `broker · ${cb.active_count ?? 0} active` : "broker · idle",
      cls: busy ? "text-sky-300" : "text-emerald-300",
    };
  }
  if (w.status === "OFFLINE") {
    return { label: "broker · offline", cls: "text-zinc-500" };
  }
  return { label: "broker · no match", cls: "text-amber-300/90" };
}

export function WorkersPanel({
  workers,
  providers,
  activeProvider,
  loading,
  admission,
  executorPools,
  brokerSummary,
  unmatchedBrokerWorkers = [],
}: Props) {
  const brokerOnline = brokerSummary?.worker_count ?? 0;
  const brokerDown = Boolean(brokerSummary?.celery_unavailable);

  return (
    <section id="workers" className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="glass rounded-xl border border-stroke p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-lg font-semibold">Workers</h2>
          <div className="text-right text-xs text-zinc-500">
            {admission != null ? (
              <>
                <div>
                  admission <span className="text-emerald-200/90">{admission.headroom ?? "—"}</span> headroom · cap{" "}
                  {admission.total_capacity ?? "—"} · inflight {admission.total_inflight ?? "—"}
                </div>
                <div className="text-[10px] text-zinc-600">
                  ONLINE {admission.online_workers ?? "—"} / rows {admission.total_worker_rows ?? "—"}
                  {brokerDown ? " · broker unreachable" : ` · Celery ${brokerOnline} online`}
                </div>
              </>
            ) : (
              "heartbeat / inflight / capacity"
            )}
          </div>
        </div>
        {executorPools?.wildcard_headroom != null || poolChips(executorPools) ? (
          <p className="mb-3 text-xs text-zinc-500">
            Executor pools: wildcard +{executorPools?.wildcard_headroom ?? 0} · {poolChips(executorPools) || "—"}
          </p>
        ) : null}
        {loading ? (
          <div className="py-8 text-sm text-zinc-400">加载 workers 中...</div>
        ) : (
          <div className="space-y-2">
            {workers.map((w, idx) => {
              const hb = formatHeartbeat(w.last_heartbeat);
              const st = (w.status ?? "").toUpperCase();
              const bb = brokerBadge(w);
              const statusCls =
                st === "ONLINE"
                  ? "text-emerald-300"
                  : st === "DRAINING"
                    ? "text-amber-300"
                    : st === "PAUSED"
                      ? "text-zinc-400"
                      : st === "ERROR" || st === "OFFLINE"
                        ? "text-red-300/90"
                        : "text-zinc-500";
              const activeTasks = w.celery_broker?.active_tasks ?? [];
              return (
                <div key={`${w.id ?? "worker"}-${idx}`} className="rounded-lg border border-stroke bg-panel2 p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium text-zinc-200">{w.worker_name ?? `worker-${w.id ?? idx}`}</div>
                    <div className="flex items-center gap-2 text-xs">
                      <span className={bb.cls}>{bb.label}</span>
                      <span className={statusCls}>{w.status ?? "unknown"}</span>
                    </div>
                  </div>
                  <div className="mt-1 text-xs text-zinc-400">
                    pool <span className="font-mono text-zinc-300">{w.worker_type ?? "—"}</span> · inflight {w.inflight ?? 0} /{" "}
                    {w.capacity ?? "—"}
                    {w.celery_broker?.celery_hostname ? (
                      <>
                        {" "}
                        · <span className="font-mono text-zinc-500">{w.celery_broker.celery_hostname}</span>
                      </>
                    ) : null}
                  </div>
                  <div
                    className={`mt-1 text-xs ${hb.stale ? "text-amber-200/90" : "text-zinc-500"}`}
                    title={`fresh if heartbeat within ${HEARTBEAT_FRESH_SEC}s`}
                  >
                    heartbeat {hb.text}
                    {hb.stale && st === "ONLINE" ? " · stale?" : ""}
                  </div>
                  {activeTasks.length > 0 ? (
                    <div className="mt-2 border-t border-stroke/60 pt-2 text-[11px] text-zinc-500">
                      {activeTasks.slice(0, 3).map((t, ti) => (
                        <div key={`${t.id ?? t.name}-${ti}`} className="truncate font-mono">
                          {t.name}
                          {t.args_preview ? ` ${t.args_preview}` : ""}
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
            {!workers.length && <div className="text-sm text-zinc-500">暂无 worker 信息</div>}
            {unmatchedBrokerWorkers.length > 0 ? (
              <div className="mt-3 rounded-lg border border-amber-500/20 bg-amber-500/5 p-3 text-xs text-amber-200/80">
                <div className="mb-1 font-medium">Celery 在线但未写入 workers 表</div>
                {unmatchedBrokerWorkers.map((bw, i) => (
                  <div key={`unmatched-${i}`} className="font-mono text-[11px] text-amber-100/70">
                    {bw.celery_hostname} · active {bw.active_count ?? 0}
                    {(bw.active_tasks?.length ?? 0) > 0 ? ` · ${bw.active_tasks?.[0]?.name}` : ""}
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </div>

      <div className="glass rounded-xl border border-stroke p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Providers · runtime</h2>
          <div className="text-xs text-zinc-500">active: {activeProvider || "—"}</div>
        </div>
        <p className="mb-3 text-xs text-zinc-600">
          下方 runtime 块来自<strong className="font-normal text-zinc-500">当前 gallery/infra API 进程</strong>内累计；多 worker 部署时请更信任{" "}
          <code className="text-zinc-500">/api/infra/metrics → inference_from_database</code>。
        </p>
        <div className="space-y-2">
          {providers.map((p) => {
            const rt = p.runtime as ProviderRuntime | null | undefined;
            const hasRt = rt && typeof rt === "object" && "requests" in rt;
            return (
              <div key={p.name} className="rounded-lg border border-stroke bg-panel2 p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <div className="font-medium text-zinc-200">{p.display_name ?? p.name}</div>
                  <div className={p.enabled ? "text-emerald-300" : "text-zinc-500"}>{p.enabled ? "enabled" : "disabled"}</div>
                </div>
                <div className="mt-1 text-xs text-zinc-400">
                  {p.model_name ?? "—"}
                  {p.fallback_model_name ? ` · fallback → ${p.fallback_model_name}` : ""}
                </div>
                {p.endpoint ? <div className="mt-1 truncate text-xs text-zinc-500">{p.endpoint}</div> : null}
                {hasRt ? (
                  <div className="mt-2 border-t border-stroke/60 pt-2 text-[11px] text-zinc-500">
                    req {rt.requests ?? 0} · fail {rt.failures ?? 0} · fb {rt.fallbacks ?? 0}
                    {rt.avg_latency_ms != null ? ` · avg ${rt.avg_latency_ms}ms` : ""}
                    {rt.last_latency_ms != null ? ` · last ${rt.last_latency_ms}ms` : ""}
                  </div>
                ) : null}
              </div>
            );
          })}
          {!providers.length && <div className="text-sm text-zinc-500">暂无 provider 信息</div>}
        </div>
      </div>
    </section>
  );
}
