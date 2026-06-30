"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  retryHistoryFromEvents,
  stageGroupBreakdownLabel,
  workerMigrationsFromEvents,
  type JobEventRow,
  type InfraJobRow,
} from "@/lib/infraJobExplorer";

type ModelRunRow = {
  id?: number;
  provider?: string | null;
  model_name?: string | null;
  status?: string | null;
  latency_ms?: number | null;
  error_type?: string | null;
  fallback_provider?: string | null;
};

type ArtifactRow = {
  kind?: string;
  path?: string;
  stage?: string | null;
  is_primary?: number | boolean;
};

type JobDetail = {
  job?: Record<string, unknown>;
  events?: JobEventRow[];
  model_runs?: ModelRunRow[];
  artifacts?: ArtifactRow[];
  primary_artifact?: ArtifactRow | null;
};

function formatTs(ts?: number | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return "—";
  }
}

type Props = {
  jobId: number;
  apiBase: string;
  workerNameById: Record<number, string>;
};

export function JobExplorerRowDetail({ jobId, apiBase, workerNameById }: Props) {
  const [data, setData] = useState<JobDetail | null>(null);
  const [stageGroupLabel, setStageGroupLabel] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${apiBase}/api/infra/jobs/${jobId}`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json() as Promise<JobDetail>;
      })
      .then((j) => {
        if (!cancelled) setData(j);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : "load failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [jobId, apiBase]);

  useEffect(() => {
    let cancelled = false;
    fetch(`${apiBase}/api/infra/jobs/${jobId}/stages`, { cache: "no-store" })
      .then(async (r) => {
        if (!r.ok) return null;
        return r.json() as Promise<{ items?: InfraJobRow[] }>;
      })
      .then((j) => {
        if (cancelled || !j?.items?.length) return;
        if (j.items.length > 1) setStageGroupLabel(stageGroupBreakdownLabel(j.items));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [jobId, apiBase]);

  if (loading) {
    return <div className="px-4 py-6 text-sm text-zinc-500">Loading execution detail…</div>;
  }
  if (error) {
    return <div className="px-4 py-6 text-sm text-red-300">{error}</div>;
  }

  const events = data?.events ?? [];
  const retryLine = retryHistoryFromEvents(events);
  const migrations = workerMigrationsFromEvents(events);
  const runs = data?.model_runs ?? [];
  const arts = data?.artifacts ?? [];
  const prim = data?.primary_artifact;

  return (
    <div className="border-t border-stroke/80 bg-zinc-950/40 px-3 py-4 sm:px-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-zinc-500">Execution detail</p>
        <Link href={`/infra/jobs/${jobId}`} className="text-xs text-sky-400 hover:underline">
          Full timeline →
        </Link>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {stageGroupLabel ? (
          <div className="lg:col-span-2">
            <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Pipeline stage breakdown</h3>
            <p className="mt-1 font-mono text-xs text-zinc-300">{stageGroupLabel}</p>
          </div>
        ) : null}
        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Event timeline</h3>
          {retryLine !== "—" ? (
            <p className="mt-1 text-xs text-amber-200/90">Retry: {retryLine}</p>
          ) : null}
          <ul className="mt-2 max-h-48 space-y-1 overflow-y-auto text-xs text-zinc-400">
            {events.map((ev) => (
              <li key={ev.id ?? `${ev.created_at}-${ev.to_status}`} className="font-mono">
                <span className="text-zinc-600">{formatTs(ev.created_at)}</span>{" "}
                <span className="text-zinc-300">
                  {ev.from_status ?? "∅"} → {ev.to_status ?? "?"}
                </span>
                {ev.message ? <span className="text-zinc-500"> · {ev.message}</span> : null}
              </li>
            ))}
            {!events.length ? <li className="text-zinc-600">No events</li> : null}
          </ul>
        </div>

        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Worker migration</h3>
          <ul className="mt-2 space-y-1 text-xs text-zinc-400">
            {migrations.map((m, i) => (
              <li key={`${m.at}-${i}`}>
                {formatTs(m.at)} · worker{" "}
                <span className="text-zinc-200">
                  {m.workerId != null ? workerNameById[m.workerId] ?? `#${m.workerId}` : "—"}
                </span>
                <span className="text-zinc-600"> · {m.note}</span>
              </li>
            ))}
            {!migrations.length ? <li className="text-zinc-600">No worker changes in event payloads</li> : null}
          </ul>
        </div>

        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Provider calls</h3>
          <ul className="mt-2 space-y-1 text-xs text-zinc-400">
            {runs.map((mr) => (
              <li key={mr.id ?? `${mr.provider}-${mr.status}`}>
                <span className="text-zinc-200">{mr.provider ?? "—"}</span> / {mr.model_name ?? "—"} ·{" "}
                <span className={mr.status === "FAILED" || mr.status === "TIMEOUT" ? "text-red-300" : "text-emerald-300/90"}>
                  {mr.status ?? "?"}
                </span>
                {mr.latency_ms != null ? ` · ${mr.latency_ms}ms` : ""}
                {mr.fallback_provider ? ` · fb ${mr.fallback_provider}` : ""}
                {mr.error_type ? ` · ${mr.error_type}` : ""}
              </li>
            ))}
            {!runs.length ? <li className="text-zinc-600">No model_runs</li> : null}
          </ul>
        </div>

        <div>
          <h3 className="text-xs font-medium uppercase tracking-wide text-zinc-500">Artifacts</h3>
          <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-xs text-zinc-400">
            {prim?.path ? (
              <li className="text-emerald-200/90">
                primary · {prim.kind ?? "—"} · <span className="font-mono text-[10px]">{prim.path}</span>
              </li>
            ) : null}
            {arts.map((a, i) => (
              <li key={`${a.path}-${i}`} className="font-mono text-[10px]">
                {a.kind ?? "file"} · {a.path ?? "—"}
              </li>
            ))}
            {!arts.length && !prim?.path ? <li className="text-zinc-600">No artifact paths</li> : null}
          </ul>
        </div>
      </div>
    </div>
  );
}
