"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getApiBase } from "@/lib/apiBase";
import { AgentRunCard } from "@/components/infra/AgentRunCard";
import { isAgentSpanLabel, type AgentRunSummary } from "@/lib/agentRun";
import { isShowcaseClient } from "@/lib/showcase";

const API_BASE = getApiBase();

type TimelineSpan = {
  id: string;
  kind: string;
  ts: number;
  label: string;
  from_status?: string | null;
  to_status?: string | null;
  duration_ms?: number | null;
  queue_wait_ms?: number | null;
  meta?: Record<string, unknown>;
};

type TimeWindow = {
  t0: number;
  t1: number;
  width_seconds: number;
};

type JobGraphNode = {
  job_id: number;
  job_type?: string;
  stage_name?: string | null;
  stage_order?: number | null;
  status?: string | null;
  total_latency_ms?: number | null;
  root_job_id?: number | null;
  parent_job_id?: number | null;
  depends_on_job_id?: number | null;
  is_terminal_failure?: boolean;
  on_critical_path?: boolean;
};

type JobGraphEdge = {
  from_job_id: number;
  to_job_id: number;
  kind?: string;
};

type JobGraphPayload = {
  scope?: string;
  root_job_id?: number;
  anchor_job_id?: number;
  nodes?: JobGraphNode[];
  edges?: JobGraphEdge[];
  stage_dag?: { node_count?: number; edges?: { from: number; to: number }[] };
  critical_path?: {
    job_ids?: number[];
    total_ms?: number | null;
    method?: string | null;
    note?: string | null;
  } | null;
  bottleneck?: {
    job_id?: number;
    total_latency_ms?: number;
    share_of_critical_path?: number | null;
  } | null;
  failure_impact?: Array<{
    failed_job_id?: number;
    status?: string;
    blocks_downstream_job_ids?: number[];
    downstream_count?: number;
  }>;
};

type JobRelationships = {
  root_job_id?: number;
  parent_job_id?: number | null;
  depends_on_job_id?: number | null;
  child_job_ids?: number[];
  dependent_job_ids?: number[];
  is_root_of_group?: boolean;
};

type TimelineBody = {
  job?: Record<string, unknown>;
  trace_id?: string | null;
  related_job_ids?: number[];
  anchor_job_id?: number;
  job_ids?: number[];
  events?: unknown[];
  model_runs?: unknown[];
  artifacts?: unknown[];
  primary_artifact?: Record<string, unknown> | null;
  worker?: Record<string, unknown> | null;
  context?: Record<string, unknown>;
  spans?: TimelineSpan[];
  time_window?: TimeWindow;
  job_relationships?: JobRelationships;
  job_graph?: JobGraphPayload;
  agent?: AgentRunSummary | null;
};

function formatTs(ts?: number | null): string {
  if (ts == null || !Number.isFinite(ts)) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

function kindClass(kind: string): string {
  if (kind === "job_event") return "bg-blue-500/20 text-blue-200 border-blue-500/40";
  if (kind === "model_run") return "bg-amber-500/20 text-amber-200 border-amber-500/40";
  if (kind === "inference_attempt") return "bg-violet-500/20 text-violet-200 border-violet-500/40";
  if (kind === "artifact") return "bg-emerald-500/20 text-emerald-200 border-emerald-500/40";
  return "bg-zinc-500/20 text-zinc-300 border-stroke";
}

function statusBorderClass(status?: string | null, onCp?: boolean): string {
  const s = status ?? "";
  if (s === "SUCCEEDED") return onCp ? "border-amber-400/80 ring-1 ring-amber-400/40" : "border-emerald-500/50";
  if (s === "QUEUED" || s === "CLAIMED" || s === "PREPROCESSING" || s === "INFERENCING" || s === "POSTPROCESSING")
    return "border-sky-500/50";
  if (s.includes("FAILED") || s === "DEAD_LETTERED" || s === "CANCELLED") return "border-red-500/60";
  return onCp ? "border-amber-400/50" : "border-stroke";
}

type Props = {
  /** e.g. `/api/infra/jobs/1/timeline` or `/api/infra/traces/my-trace` */
  apiPath: string;
  backHref?: string;
  title?: string;
  /** SSR / showcase snapshot — used when live API 404s or in SHOWCASE_MODE. */
  fallbackData?: TimelineBody | null;
};

export function JobTimeline({
  apiPath,
  backHref = "/infra",
  title = "Job timeline",
  fallbackData = null,
}: Props) {
  const showcase = isShowcaseClient();
  const [data, setData] = useState<TimelineBody | null>(fallbackData);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(!(showcase && fallbackData));

  useEffect(() => {
    if (showcase && fallbackData) {
      setData(fallbackData);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    const u = apiPath.startsWith("http") ? apiPath : `${API_BASE.replace(/\/$/, "")}/${apiPath.replace(/^\//, "")}`;
    (async () => {
      setLoading(true);
      try {
        const r = await fetch(u, { cache: "no-store" });
        if (!r.ok) {
          if (fallbackData) {
            if (!cancelled) {
              setData(fallbackData);
              setError(null);
            }
            return;
          }
          if (r.status === 404) throw new Error("未找到该 job / trace");
          throw new Error(`request failed: ${r.status}`);
        }
        const j: TimelineBody = await r.json();
        if (!cancelled) {
          setData(j);
          setError(null);
        }
      } catch (e: unknown) {
        if (!cancelled) {
          if (fallbackData) {
            setData(fallbackData);
            setError(null);
          } else {
            setError(e instanceof Error ? e.message : "load failed");
          }
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apiPath, fallbackData, showcase]);

  const tw = data?.time_window;
  const t0 = tw?.t0 ?? 0;
  const t1 = tw?.t1 ?? t0 + 1;
  const rangeSec = Math.max(1, t1 - t0);
  const job = data?.job ?? {};
  const jid = Number(job.id);
  const traceId = data?.trace_id;
  const anchor = data?.anchor_job_id;
  const traceJobs = data?.job_ids;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div className="min-w-0">
          <nav className="mb-2 flex flex-wrap items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-500" aria-label="Breadcrumb">
            <Link href="/infra" className="hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50">
              Infra
            </Link>
            <span aria-hidden>/</span>
            <Link href="/infra/brain" className="hover:text-zinc-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50">
              Brain
            </Link>
            <span aria-hidden>/</span>
            <span className="truncate text-zinc-400">{title}</span>
          </nav>
          <div className="text-xs uppercase tracking-[0.2em] text-zinc-500">{title}</div>
          <h1 className="text-2xl font-semibold tracking-tight text-zinc-100">
            {Number.isFinite(jid) ? `Job #${jid}` : "—"}
            {typeof anchor === "number" && jid !== anchor ? (
              <span className="ml-2 text-sm font-normal text-amber-200/80">· trace anchor #{anchor}</span>
            ) : null}
            {job.enqueued_at != null && (
              <span className="ml-2 text-sm font-normal text-zinc-500">
                · 入队 {formatTs(job.enqueued_at as number)}
              </span>
            )}
          </h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            href="/studio"
            className="rounded-lg border border-stroke px-3 py-2 text-xs text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50"
          >
            Studio
          </Link>
          <Link
            href={backHref}
            className="rounded-lg border border-stroke px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-400/50"
          >
            返回 Console
          </Link>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300">{error}</div>
      ) : null}

      {loading ? <div className="text-sm text-zinc-500">加载 timeline…</div> : null}

      {data && !loading ? (
        <>
          <section className="glass rounded-xl border border-stroke p-4">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">概览</h2>
            <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-zinc-500">Type</dt>
                <dd className="text-zinc-200">{(job.job_type as string) ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Status</dt>
                <dd className="text-zinc-200">{(job.status as string) ?? "—"}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">Attempts</dt>
                <dd className="text-zinc-200">
                  {String(job.attempts ?? 0)} / {job.max_attempts != null ? String(job.max_attempts) : "—"}
                  {(job.status as string) === "FAILED_RETRYABLE" ? (
                    <span className="ml-2 text-xs text-amber-200/80">（仍可能自动重试）</span>
                  ) : null}
                  {(job.status as string) === "DEAD_LETTERED" ? (
                    <span className="ml-2 text-xs text-red-300/90">（需 POST …/retry）</span>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">Trace</dt>
                <dd className="break-all font-mono text-xs text-zinc-300">
                  {traceId ? (
                    <Link
                      className="text-sky-400 hover:underline"
                      href={`/infra/traces/${encodeURIComponent(traceId)}`}
                    >
                      {traceId}
                    </Link>
                  ) : (
                    "—"
                  )}
                </dd>
              </div>
              <div>
                <dt className="text-zinc-500">Worker</dt>
                <dd className="text-zinc-200">
                  {data.worker
                    ? `#${data.worker.id} ${String(data.worker.worker_name ?? "")} (${String(data.worker.status ?? "")})`
                    : (job.worker_id != null ? `#${String(job.worker_id)}` : "—")}
                </dd>
              </div>
            </dl>
            {Array.isArray(traceJobs) && traceJobs.length > 1 ? (
              <p className="mt-2 text-xs text-zinc-500">
                同 trace 的 jobs: {traceJobs.map((x) => (x === anchor ? <strong key={x}>#{x} </strong> : <span key={x}>#{x} </span>))}
                {typeof anchor === "number" && <span className="ml-1">(当前为 anchor #{anchor})</span>}
              </p>
            ) : null}
          </section>

          {data.agent?.is_agent_run ? <AgentRunCard agent={data.agent} apiBase={API_BASE} /> : null}

          {data.job_graph && (data.job_graph.nodes?.length ?? 0) > 0 ? (
            <section className="glass rounded-xl border border-stroke p-4">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">
                Stage DAG · Critical path
              </h2>
              <p className="mb-3 text-xs text-zinc-500">
                同 <span className="font-mono text-zinc-400">root_job_id</span> 下的依赖图；边为{" "}
                <span className="text-zinc-400">depends_on</span>（上游 → 下游）。橙框为 longest weighted path
                上的阶段（用各 job 的 <span className="text-zinc-400">total_latency_ms</span> 加权）。
              </p>

              {data.job_relationships ? (
                <dl className="mb-4 grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                  <div>
                    <dt className="text-zinc-600">Root</dt>
                    <dd className="font-mono text-zinc-300">#{data.job_relationships.root_job_id ?? "—"}</dd>
                  </div>
                  <div>
                    <dt className="text-zinc-600">Parent / depends_on</dt>
                    <dd className="font-mono text-zinc-300">
                      {data.job_relationships.parent_job_id != null
                        ? `#${data.job_relationships.parent_job_id}`
                        : "—"}{" "}
                      /{" "}
                      {data.job_relationships.depends_on_job_id != null
                        ? `#${data.job_relationships.depends_on_job_id}`
                        : "—"}
                    </dd>
                  </div>
                  <div className="sm:col-span-2">
                    <dt className="text-zinc-600">下游（直接依赖当前 job）</dt>
                    <dd className="text-zinc-300">
                      {(data.job_relationships.dependent_job_ids?.length ?? 0) > 0
                        ? data.job_relationships.dependent_job_ids?.map((id) => (
                            <Link key={id} className="mr-2 font-mono text-sky-400 hover:underline" href={`/infra/jobs/${id}`}>
                              #{id}
                            </Link>
                          ))
                        : "—"}
                    </dd>
                  </div>
                </dl>
              ) : null}

              {data.job_graph.critical_path?.total_ms != null && data.job_graph.critical_path.total_ms > 0 ? (
                <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-zinc-300">
                  <span className="text-amber-200/90">Longest path Σlatency</span> ≈{" "}
                  <strong>{data.job_graph.critical_path.total_ms}ms</strong>
                  {data.job_graph.bottleneck?.job_id != null ? (
                    <span className="ml-2 text-zinc-500">
                      · 单步最慢:{" "}
                      <Link className="text-sky-400 hover:underline" href={`/infra/jobs/${data.job_graph.bottleneck.job_id}`}>
                        #{data.job_graph.bottleneck.job_id}
                      </Link>{" "}
                      ({data.job_graph.bottleneck.total_latency_ms ?? "—"}ms
                      {typeof data.job_graph.bottleneck.share_of_critical_path === "number"
                        ? `, ~${(data.job_graph.bottleneck.share_of_critical_path * 100).toFixed(0)}% of path`
                        : ""}
                      )
                    </span>
                  ) : null}
                </div>
              ) : null}

              <div className="mb-4 flex flex-wrap items-stretch gap-1">
                {[...(data.job_graph.nodes ?? [])]
                  .sort((a, b) => {
                    const ao = a.stage_order ?? 0;
                    const bo = b.stage_order ?? 0;
                    if (ao !== bo) return ao - bo;
                    return a.job_id - b.job_id;
                  })
                  .map((n, idx, arr) => (
                    <div key={n.job_id} className="flex items-center gap-1">
                      <Link
                        href={`/infra/jobs/${n.job_id}`}
                        title={`${n.stage_name ?? n.job_type ?? "job"} · ${n.status ?? "?"}`}
                        className={`min-w-[5.5rem] rounded-lg border px-2 py-1.5 text-center text-[11px] leading-tight transition hover:bg-zinc-800/80 ${statusBorderClass(n.status ?? undefined, n.on_critical_path)}`}
                      >
                        <div className="font-mono text-sky-300">#{n.job_id}</div>
                        <div className="truncate text-zinc-500">{n.stage_name ?? n.job_type ?? "—"}</div>
                        {n.total_latency_ms != null ? (
                          <div className="text-amber-200/70">{n.total_latency_ms}ms</div>
                        ) : (
                          <div className="text-zinc-600">—</div>
                        )}
                      </Link>
                      {idx < arr.length - 1 ? (
                        <span className="select-none px-0.5 text-zinc-600" aria-hidden>
                          →
                        </span>
                      ) : null}
                    </div>
                  ))}
              </div>

              {(data.job_graph.failure_impact?.length ?? 0) > 0 ? (
                <div className="mb-3 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-xs">
                  <div className="mb-1 font-medium text-red-300/90">失败对下游的影响</div>
                  <ul className="list-inside list-disc space-y-1 text-zinc-400">
                    {data.job_graph.failure_impact?.map((fi) => (
                      <li key={`${fi.failed_job_id}-${fi.status}`}>
                        Job #{fi.failed_job_id} ({fi.status}) 阻塞{" "}
                        {fi.downstream_count ?? fi.blocks_downstream_job_ids?.length ?? 0} 个下游:{" "}
                        {(fi.blocks_downstream_job_ids ?? []).map((id) => (
                          <Link key={id} className="font-mono text-red-200/90 hover:underline" href={`/infra/jobs/${id}`}>
                            #{id}{" "}
                          </Link>
                        ))}
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}

              <div className="overflow-x-auto rounded border border-stroke/60">
                <table className="min-w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-stroke text-zinc-500">
                      <th className="px-2 py-1.5">Job</th>
                      <th className="px-2 py-1.5">Stage</th>
                      <th className="px-2 py-1.5">Status</th>
                      <th className="px-2 py-1.5">Latency</th>
                      <th className="px-2 py-1.5">Path</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...(data.job_graph.nodes ?? [])]
                      .sort((a, b) => (a.stage_order ?? 0) - (b.stage_order ?? 0))
                      .map((n) => (
                        <tr key={n.job_id} className="border-b border-stroke/40 text-zinc-300">
                          <td className="px-2 py-1 font-mono">
                            <Link className="text-sky-400 hover:underline" href={`/infra/jobs/${n.job_id}`}>
                              #{n.job_id}
                            </Link>
                          </td>
                          <td className="px-2 py-1">{n.stage_name ?? "—"}</td>
                          <td className="px-2 py-1">{n.status ?? "—"}</td>
                          <td className="px-2 py-1">{n.total_latency_ms != null ? `${n.total_latency_ms}ms` : "—"}</td>
                          <td className="px-2 py-1">{n.on_critical_path ? "★ CP" : "—"}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            </section>
          ) : null}

          <section className="glass rounded-xl border border-stroke p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">Waterfall（相对时间窗）</h2>
            <p className="mb-3 text-xs text-zinc-500">
              窗口: {formatTs(t0)} — {formatTs(t1)}（约 {rangeSec}s）。横轴为 wall clock；有 duration 的 span 画条，其余画刻度线。
            </p>
            <div className="relative h-12 w-full overflow-hidden rounded border border-stroke/80 bg-zinc-900/60">
              {(data.spans ?? [])
                .filter((s) => s.duration_ms && s.duration_ms > 0)
                .map((s) => {
                  const start = (s.ts - t0) / rangeSec;
                  const durSec = (s.duration_ms ?? 0) / 1000;
                  const w = (durSec / rangeSec) * 100;
                  const left = Math.max(0, start * 100);
                  const barCls =
                    s.kind === "model_run"
                      ? "bg-amber-500/75"
                      : s.kind === "inference_attempt"
                        ? "bg-violet-500/70"
                        : s.kind === "job_event"
                          ? "bg-sky-500/65"
                          : "bg-emerald-500/60";
                  return (
                    <div
                      key={`bar-${s.id}`}
                      title={`${s.kind}: ${s.label} (${durSec.toFixed(2)}s)`}
                      className={`absolute top-1 h-3 rounded-sm ${barCls}`}
                      style={{
                        left: `${left}%`,
                        width: `${Math.min(100 - left, Math.max(0.5, w))}%`,
                        minWidth: "2px",
                      }}
                    />
                  );
                })}
              {(data.spans ?? [])
                .filter((s) => !s.duration_ms || (s.duration_ms ?? 0) <= 0)
                .map((s) => {
                  const pos = ((s.ts - t0) / rangeSec) * 100;
                  const dotCls =
                    s.kind === "job_event"
                      ? "bg-sky-400/90"
                      : s.kind === "artifact"
                        ? "bg-emerald-400/90"
                        : s.kind === "inference_attempt"
                          ? "bg-violet-400/85"
                          : "bg-zinc-500/80";
                  return (
                    <div
                      key={`dot-${s.id}`}
                      title={`${s.kind}: ${s.label}`}
                      className={`absolute top-6 h-2 w-0.5 -translate-x-1/2 ${dotCls}`}
                      style={{ left: `${Math.max(0, Math.min(100, pos))}%` }}
                    />
                  );
                })}
            </div>
            <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-zinc-600">
              <li>
                <span className="inline-block h-2 w-4 rounded-sm bg-sky-500/65 align-middle" /> job_event（条若存在 duration）
              </li>
              <li>
                <span className="inline-block h-2 w-4 rounded-sm bg-amber-500/75 align-middle" /> model_run
              </li>
              <li>
                <span className="inline-block h-2 w-4 rounded-sm bg-violet-500/70 align-middle" /> inference_attempt
              </li>
              <li>
                <span className="inline-block h-2 w-4 rounded-sm bg-emerald-500/60 align-middle" /> 其它（含 artifact）
              </li>
              <li className="text-zinc-500">竖线为「无持续时间」打点</li>
            </ul>
          </section>

          <section className="glass rounded-xl border border-stroke p-4">
            <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-zinc-500">时间线（有序）</h2>
            <ul className="space-y-2">
              {(data.spans ?? []).map((s) => {
                const agentSpan = s.kind === "job_event" && isAgentSpanLabel(s.label);
                const escalatedSpan = agentSpan && s.label.includes("[escalated]");
                const finalizeSpan = agentSpan && s.label.startsWith("agent finalize");
                const badgeClass = escalatedSpan
                  ? "bg-violet-500/20 text-violet-200 border-violet-500/50"
                  : finalizeSpan
                    ? "bg-emerald-500/20 text-emerald-200 border-emerald-500/50"
                    : agentSpan
                      ? "bg-sky-500/15 text-sky-200 border-sky-500/40"
                      : kindClass(s.kind);
                const badgeText = escalatedSpan
                  ? "↑ agent escalate"
                  : finalizeSpan
                    ? "✓ agent finalize"
                    : agentSpan
                      ? "agent analyze"
                      : s.kind.replaceAll("_", " ");
                return (
                <li
                  key={s.id}
                  className={`flex flex-col gap-1 rounded-lg border px-3 py-2 sm:flex-row sm:items-start sm:gap-3 ${
                    escalatedSpan ? "border-violet-500/30 bg-violet-950/15" : "border-stroke/60 bg-panel2/30"
                  }`}
                >
                  <div className="w-44 shrink-0 text-xs text-zinc-500">
                    {formatTs(s.ts)}
                    <span className="ml-1 font-mono text-[10px] text-zinc-600">
                      +{(s.ts - t0).toLocaleString()}s
                    </span>
                  </div>
                  <div
                    className={`inline-flex w-fit shrink-0 items-center rounded border px-1.5 py-0.5 text-[10px] font-medium uppercase ${badgeClass}`}
                  >
                    {badgeText}
                  </div>
                  <div className="min-w-0 flex-1 text-sm text-zinc-200">
                    {s.label}
                    {s.queue_wait_ms != null && s.kind === "model_run" ? (
                      <span className="ml-2 text-xs text-zinc-500">queue_wait {s.queue_wait_ms}ms</span>
                    ) : null}
                    {s.duration_ms != null && (s.kind === "model_run" || s.kind === "inference_attempt") ? (
                      <span className="ml-2 text-xs text-amber-200/80">duration ~{s.duration_ms}ms</span>
                    ) : null}
                  </div>
                </li>
                );
              })}
            </ul>
            {!(data.spans ?? []).length && <p className="text-sm text-zinc-500">暂无 spans</p>}
          </section>

          <section className="glass rounded-xl border border-stroke p-4">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-zinc-500">Latency / 运维上下文</h2>
            <pre className="max-h-48 overflow-auto rounded border border-stroke/60 bg-zinc-950/80 p-3 text-xs text-zinc-300">
              {JSON.stringify(data.context ?? {}, null, 2)}
            </pre>
          </section>
        </>
      ) : null}
    </div>
  );
}
