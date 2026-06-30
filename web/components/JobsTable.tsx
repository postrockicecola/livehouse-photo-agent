"use client";

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import type { InfraWorkerRow } from "@/components/WorkersPanel";
import { JobExplorerRowDetail } from "@/components/infra/JobExplorerRowDetail";
import {
  STATUS_FILTER_OPTIONS,
  buildJobsQuery,
  clientJobMatchesSearch,
  formatMs,
  isFailedJob,
  isRetryHighlighted,
  isRunningJob,
  retryHistoryShortFromJob,
  runDurationMs,
  stageGroupBreakdownLabel,
  stageGroupRootId,
  stageBreakdownLabel,
  type InfraJobRow,
  type StatusFilterKey,
} from "@/lib/infraJobExplorer";

type Props = {
  apiBase: string;
  byStatus: Record<string, number>;
  workers: InfraWorkerRow[];
  loading?: boolean;
};

function formatTs(ts?: number | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

const TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED_PERMANENT", "DEAD_LETTERED", "CANCELLED"]);

function isAgentJob(job: InfraJobRow): boolean {
  return (job.job_type ?? "").startsWith("CURATE");
}

function rowVisualClass(job: InfraJobRow): string {
  if (isFailedJob(job.status)) return "bg-red-950/25 border-l-2 border-l-red-500/70";
  if (isRunningJob(job.status)) return "bg-emerald-950/15 border-l-2 border-l-emerald-500/50";
  if (isRetryHighlighted(job)) return "bg-amber-950/20 border-l-2 border-l-amber-500/60";
  return "border-l-2 border-l-transparent";
}

function StatusBadge({ status }: { status?: string | null }) {
  const s = status ?? "";
  const running = isRunningJob(s);
  const failed = isFailedJob(s);
  const cls = failed
    ? "text-red-300"
    : running
      ? "text-emerald-300"
      : s === "SUCCEEDED"
        ? "text-emerald-200/80"
        : s === "QUEUED"
          ? "text-zinc-400"
          : "text-zinc-300";
  return (
    <span className={`inline-flex items-center gap-1.5 font-mono text-xs ${cls}`}>
      {running ? (
        <span className="relative flex h-2 w-2">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400/70" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-400" />
        </span>
      ) : null}
      {s || "—"}
    </span>
  );
}

export function JobsTable({ apiBase, byStatus, workers, loading: parentLoading }: Props) {
  const [statusFilter, setStatusFilter] = useState<StatusFilterKey>("ALL");
  const [agentOnly, setAgentOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [traceQuery, setTraceQuery] = useState("");
  const [traceDebounced, setTraceDebounced] = useState("");
  const [jobs, setJobs] = useState<InfraJobRow[]>([]);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [stageLabelByRoot, setStageLabelByRoot] = useState<Record<number, string>>({});
  const [actionBusyId, setActionBusyId] = useState<number | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  const workerNameById = useMemo(() => {
    const m: Record<number, string> = {};
    for (const w of workers) {
      const wid = w.id;
      if (wid != null && w.worker_name) m[wid] = w.worker_name;
    }
    return m;
  }, [workers]);

  useEffect(() => {
    const t = setTimeout(() => setTraceDebounced(traceQuery), 400);
    return () => clearTimeout(t);
  }, [traceQuery]);

  const loadJobs = useCallback(async () => {
    setFetching(true);
    setFetchError(null);
    try {
      const url = buildJobsQuery(apiBase, { statusFilter, traceQuery: traceDebounced, limit: 80 });
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`jobs ${r.status}`);
      const j = (await r.json()) as { items?: InfraJobRow[] };
      setJobs(j.items ?? []);
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : "failed to load jobs");
    } finally {
      setFetching(false);
    }
  }, [apiBase, statusFilter, traceDebounced]);

  useEffect(() => {
    loadJobs();
    const t = setInterval(loadJobs, 5000);
    return () => clearInterval(t);
  }, [loadJobs]);

  const postJobAction = useCallback(
    async (jobId: number, action: "retry" | "cancel") => {
      if (!window.confirm(`${action === "retry" ? "Retry" : "Cancel"} job #${jobId}?`)) return;
      setActionBusyId(jobId);
      setActionNotice(null);
      try {
        const r = await fetch(`${apiBase}/api/infra/jobs/${jobId}/${action}`, { method: "POST" });
        const j = (await r.json().catch(() => ({}))) as { detail?: string; status?: string };
        if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`);
        setActionNotice(`#${jobId} ${action} → ${j.status ?? "ok"}`);
        loadJobs();
      } catch (e) {
        setActionNotice(e instanceof Error ? e.message : `${action} failed`);
      } finally {
        setActionBusyId(null);
      }
    },
    [apiBase, loadJobs],
  );

  useEffect(() => {
    const roots = new Set<number>();
    for (const job of jobs) {
      const root = stageGroupRootId(job);
      if (root != null) roots.add(root);
    }
    const missing = [...roots].filter((r) => stageLabelByRoot[r] == null);
    if (!missing.length) return;
    let cancelled = false;
    (async () => {
      const updates: Record<number, string> = {};
      await Promise.all(
        missing.map(async (rootId) => {
          try {
            const r = await fetch(`${apiBase}/api/infra/jobs/${rootId}/stages`, { cache: "no-store" });
            if (!r.ok) return;
            const j = (await r.json()) as { items?: InfraJobRow[] };
            const items = j.items ?? [];
            if (items.length > 1) {
              updates[rootId] = stageGroupBreakdownLabel(items);
            }
          } catch {
            /* ignore */
          }
        }),
      );
      if (!cancelled && Object.keys(updates).length) {
        setStageLabelByRoot((prev) => ({ ...prev, ...updates }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobs, apiBase, stageLabelByRoot]);

  const filteredJobs = useMemo(
    () =>
      jobs.filter(
        (job) => (!agentOnly || isAgentJob(job)) && clientJobMatchesSearch(job, search, workerNameById),
      ),
    [jobs, search, workerNameById, agentOnly],
  );

  const statusEntries = Object.entries(byStatus).sort((a, b) => b[1] - a[1]);
  const loading = parentLoading || fetching;

  return (
    <section className="glass rounded-xl border border-stroke p-4">
      <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Job Explorer</h2>
          <p className="mt-1 text-xs text-zinc-500">Execution engine view · expand a row for timeline, workers, providers, artifacts</p>
        </div>
        <button
          type="button"
          onClick={() => loadJobs()}
          className="rounded-lg border border-stroke px-3 py-1.5 text-xs text-zinc-400 hover:bg-zinc-800"
        >
          Refresh
        </button>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {STATUS_FILTER_OPTIONS.map((opt) => (
          <button
            key={opt.key}
            type="button"
            onClick={() => setStatusFilter(opt.key)}
            className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
              statusFilter === opt.key
                ? "border-sky-500/50 bg-sky-950/30 text-sky-200"
                : "border-stroke bg-panel2 text-zinc-400 hover:text-zinc-200"
            }`}
          >
            {opt.label}
          </button>
        ))}
        <span className="mx-1 self-center text-zinc-700">|</span>
        <button
          type="button"
          onClick={() => setAgentOnly((v) => !v)}
          className={`rounded-md border px-2.5 py-1 text-xs transition-colors ${
            agentOnly
              ? "border-violet-500/50 bg-violet-950/30 text-violet-200"
              : "border-stroke bg-panel2 text-zinc-400 hover:text-zinc-200"
          }`}
          title="Show only agentic curation jobs (CURATE_*)"
        >
          🤖 Agent
        </button>
      </div>

      <div className="mb-4 grid gap-2 sm:grid-cols-2">
        <input
          type="search"
          placeholder="Search session, trace, worker, job id…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="rounded-lg border border-stroke bg-panel2 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
        />
        <input
          type="search"
          placeholder="Trace id filter (server-side substring)…"
          value={traceQuery}
          onChange={(e) => setTraceQuery(e.target.value)}
          className="rounded-lg border border-stroke bg-panel2 px-3 py-2 text-sm text-zinc-200 placeholder:text-zinc-600"
        />
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {statusEntries.length ? (
          statusEntries.map(([status, count]) => (
            <div key={status} className="rounded-md border border-stroke bg-panel2 px-2 py-1 text-xs text-zinc-300">
              <span className="text-zinc-500">{status}</span> {count}
            </div>
          ))
        ) : null}
      </div>

      {fetchError ? <div className="mb-3 text-sm text-red-300">{fetchError}</div> : null}
      {actionNotice ? (
        <div className="mb-3 rounded border border-stroke/80 bg-zinc-900/60 px-3 py-2 text-xs text-zinc-300">{actionNotice}</div>
      ) : null}

      {loading && !filteredJobs.length ? (
        <div className="py-8 text-sm text-zinc-400">Loading jobs…</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-[1100px] w-full text-left text-sm">
            <thead>
              <tr className="border-b border-stroke text-[10px] uppercase tracking-wide text-zinc-500">
                <th className="w-8 px-1 py-2" />
                <th className="px-2 py-2">Job</th>
                <th className="px-2 py-2">Trace / Session</th>
                <th className="px-2 py-2">Status</th>
                <th className="px-2 py-2">Worker</th>
                <th className="px-2 py-2">Queue wait</th>
                <th className="px-2 py-2">Run duration</th>
                <th className="px-2 py-2">Stage breakdown</th>
                <th className="px-2 py-2">Retry history</th>
                <th className="px-2 py-2">Updated</th>
                <th className="px-2 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredJobs.map((job, idx) => {
                const jid = job.id;
                const open = jid != null && expandedId === jid;
                const wLabel =
                  job.worker_id != null ? workerNameById[job.worker_id] ?? `#${job.worker_id}` : "—";
                return (
                  <Fragment key={`${jid ?? "job"}-${idx}`}>
                    <tr
                      className={`cursor-pointer border-b border-stroke/60 text-zinc-300 transition-colors hover:bg-zinc-900/40 ${rowVisualClass(job)}`}
                      onClick={() => {
                        if (jid == null) return;
                        setExpandedId(open ? null : jid);
                      }}
                    >
                      <td className="px-1 py-2 text-center text-zinc-600">{open ? "▼" : "▶"}</td>
                      <td className="px-2 py-2 font-medium text-zinc-200">
                        {jid != null ? (
                          <Link
                            className="text-sky-400 hover:underline"
                            href={`/infra/jobs/${jid}`}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {jid}
                          </Link>
                        ) : (
                          "—"
                        )}
                        <div className="flex items-center gap-1 text-[10px] text-zinc-600">
                          {job.job_type ?? ""}
                          {isAgentJob(job) ? (
                            <span className="rounded border border-violet-500/40 bg-violet-950/30 px-1 py-px text-[9px] font-medium text-violet-200">
                              🤖 agent
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="max-w-[11rem] px-2 py-2 font-mono text-[11px]">
                        {job.trace_id ? (
                          <Link
                            className="block truncate text-zinc-400 hover:text-sky-400"
                            href={`/infra/traces/${encodeURIComponent(job.trace_id)}`}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {job.trace_id}
                          </Link>
                        ) : (
                          <span className="text-zinc-600">—</span>
                        )}
                        <div className="text-zinc-600">sess {job.session_id ?? "—"}</div>
                      </td>
                      <td className="px-2 py-2">
                        <StatusBadge status={job.status} />
                      </td>
                      <td className="px-2 py-2 text-xs">{wLabel}</td>
                      <td className="px-2 py-2 font-mono text-xs tabular-nums">{formatMs(job.queue_wait_ms)}</td>
                      <td className="px-2 py-2 font-mono text-xs tabular-nums">{formatMs(runDurationMs(job))}</td>
                      <td className="max-w-[14rem] px-2 py-2 text-[11px] leading-snug text-zinc-400">
                        {(() => {
                          const root = stageGroupRootId(job);
                          if (root != null && stageLabelByRoot[root]) return stageLabelByRoot[root];
                          return stageBreakdownLabel(job);
                        })()}
                      </td>
                      <td className="max-w-[12rem] px-2 py-2 text-[11px] leading-snug text-amber-200/90">
                        {retryHistoryShortFromJob(job)}
                      </td>
                      <td className="px-2 py-2 text-xs text-zinc-500">{formatTs(job.updated_at)}</td>
                      <td className="whitespace-nowrap px-2 py-2" onClick={(e) => e.stopPropagation()}>
                        {jid != null ? (
                          <div className="flex gap-1.5">
                            {(() => {
                              const s = job.status ?? "";
                              const terminal = TERMINAL_STATUSES.has(s);
                              const canRetry = terminal || s === "FAILED_RETRYABLE";
                              const canCancel = !terminal && s !== "";
                              const busy = actionBusyId === jid;
                              return (
                                <>
                                  {canRetry ? (
                                    <button
                                      type="button"
                                      disabled={busy}
                                      onClick={() => postJobAction(jid, "retry")}
                                      className="rounded border border-amber-600/50 bg-amber-950/30 px-2 py-1 text-[11px] text-amber-100 hover:bg-amber-900/40 disabled:opacity-40"
                                    >
                                      {busy ? "…" : "Retry"}
                                    </button>
                                  ) : null}
                                  {canCancel ? (
                                    <button
                                      type="button"
                                      disabled={busy}
                                      onClick={() => postJobAction(jid, "cancel")}
                                      className="rounded border border-stroke bg-panel2 px-2 py-1 text-[11px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
                                    >
                                      {busy ? "…" : "Cancel"}
                                    </button>
                                  ) : null}
                                  {!canRetry && !canCancel ? <span className="text-[11px] text-zinc-600">—</span> : null}
                                </>
                              );
                            })()}
                          </div>
                        ) : (
                          <span className="text-[11px] text-zinc-600">—</span>
                        )}
                      </td>
                    </tr>
                    {open && jid != null ? (
                      <tr className="border-b border-stroke/60">
                        <td colSpan={11} className="p-0">
                          <JobExplorerRowDetail jobId={jid} apiBase={apiBase} workerNameById={workerNameById} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
          {!filteredJobs.length && <div className="py-6 text-sm text-zinc-500">No jobs match filters</div>}
        </div>
      )}
    </section>
  );
}

// Re-export type for other modules
export type { InfraJobRow };
