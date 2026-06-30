"use client";

import Link from "next/link";
import { useState } from "react";

export type DeadLetterJobRow = {
  id?: number;
  job_type?: string;
  status?: string;
  attempts?: number | null;
  max_attempts?: number | null;
  updated_at?: number | null;
  trace_id?: string | null;
};

type Props = {
  items: DeadLetterJobRow[];
  loading: boolean;
  apiBase: string;
  /** When true, omit outer section chrome (for Failure Center embedding). */
  embedded?: boolean;
};

function formatTs(ts?: number | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

export function DeadLetterPanel({ items, loading, apiBase, embedded }: Props) {
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const postRetry = async (jobId: number) => {
    setBusyId(jobId);
    setNotice(null);
    try {
      const r = await fetch(`${apiBase}/api/infra/jobs/${jobId}/retry`, { method: "POST" });
      const j = (await r.json().catch(() => ({}))) as { detail?: string; status?: string };
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `HTTP ${r.status}`);
      setNotice(`#${jobId} 已手动重试 → ${j.status ?? "QUEUED"}`);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : "retry 失败");
    } finally {
      setBusyId(null);
    }
  };

  const body = (
    <>
      {!embedded ? (
        <div className="mb-2 flex flex-wrap items-end justify-between gap-2">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Dead letter · 手动重试</h2>
            <p className="mt-1 max-w-3xl text-xs text-zinc-500">
              <span className="text-amber-200/80">DEAD_LETTERED</span>：自动重试已耗尽，不会影响调度 headroom，但会占用 SSOT 行直至{" "}
              <code className="rounded bg-zinc-800 px-1 py-0.5 text-[10px]">POST /api/infra/jobs/&lt;id&gt;/retry</code>{" "}
              或人工清理。<span className="text-zinc-600">FAILED_RETRYABLE</span> 仍可由执行器按策略重试。
            </p>
          </div>
        </div>
      ) : (
        <p className="mb-3 text-xs text-zinc-500">
          <span className="text-amber-200/80">Dead-letter queue</span> — manual{" "}
          <code className="rounded bg-zinc-800 px-1 py-0.5 text-[10px]">POST …/retry</code>
        </p>
      )}

      {notice ? <div className="mb-3 rounded border border-stroke/80 bg-zinc-900/60 px-3 py-2 text-xs text-zinc-300">{notice}</div> : null}

      {loading ? (
        <div className="py-6 text-sm text-zinc-400">加载 dead-letter…</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full text-left text-sm">
            <thead>
              <tr className="border-b border-stroke text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-2 py-2">Job</th>
                <th className="px-2 py-2">Type</th>
                <th className="px-2 py-2">Attempts</th>
                <th className="px-2 py-2">Trace</th>
                <th className="px-2 py-2">Updated</th>
                <th className="px-2 py-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.map((job: DeadLetterJobRow, idx) => {
                const jid = job.id;
                return (
                  <tr key={`${jid ?? "x"}-${idx}`} className="border-b border-stroke/60 text-zinc-300">
                    <td className="px-2 py-2 font-mono">
                      {jid != null ? (
                        <Link className="text-sky-400 hover:underline" href={`/infra/jobs/${jid}`}>
                          #{jid}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-2 py-2">{job.job_type ?? "—"}</td>
                    <td className="px-2 py-2">
                      {(job.attempts ?? 0)}/{job.max_attempts ?? "—"}
                    </td>
                    <td className="max-w-[12rem] truncate px-2 py-2 font-mono text-xs">
                      {job.trace_id ? (
                        <Link className="text-zinc-400 hover:text-sky-400" href={`/infra/traces/${encodeURIComponent(job.trace_id)}`}>
                          {job.trace_id}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-2 py-2 text-xs text-zinc-500">{formatTs(job.updated_at)}</td>
                    <td className="px-2 py-2 space-x-2 whitespace-nowrap">
                      {jid != null ? (
                        <>
                          <button
                            type="button"
                            disabled={busyId === jid}
                            className="rounded border border-amber-600/50 bg-amber-950/40 px-2 py-1 text-xs text-amber-100 hover:bg-amber-900/50 disabled:opacity-40"
                            onClick={() => postRetry(jid)}
                          >
                            {busyId === jid ? "…" : "Retry"}
                          </button>
                          <Link className="text-xs text-zinc-500 hover:text-sky-400" href={`/infra/jobs/${jid}`}>
                            Timeline
                          </Link>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!items.length ? <div className="py-6 text-sm text-zinc-500">暂无 DEAD_LETTERED 任务</div> : null}
        </div>
      )}
    </>
  );

  if (embedded) return body;

  return (
    <section id="dead-letter" className="glass rounded-xl border border-stroke border-amber-900/30 p-4">
      {body}
    </section>
  );
}
