"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  buildPipelineTimelineEntries,
  formatTimelineTs,
  shortTraceId,
  TIMELINE_TONE_CLASS,
  TIMELINE_TONE_DOT,
  type InfraRuntimeEventRow,
} from "@/lib/infraPipelineTimeline";
import { ControlPlaneSection, LivePulse } from "./ControlPlaneSection";

type Props = {
  events: InfraRuntimeEventRow[];
  loading?: boolean;
  limit?: number;
};

export function LivePipelineTimeline({ events, loading, limit = 20 }: Props) {
  const entries = useMemo(() => buildPipelineTimelineEntries(events, limit), [events, limit]);
  const listRef = useRef<HTMLUListElement>(null);
  const prevTopId = useRef<number | null>(null);
  const [refreshFlash, setRefreshFlash] = useState(0);

  useEffect(() => {
    const top = entries[0]?.id ?? null;
    if (top != null && prevTopId.current != null && top !== prevTopId.current) {
      listRef.current?.scrollTo({ top: 0, behavior: "smooth" });
    }
    prevTopId.current = top;
  }, [entries]);

  useEffect(() => {
    const t = setInterval(() => setRefreshFlash((n) => n + 1), 3000);
    return () => clearInterval(t);
  }, []);

  return (
    <ControlPlaneSection eyebrow="Activity" title="Pipeline Timeline" right={<LivePulse />}>
      <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] uppercase tracking-wide text-zinc-700">
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${TIMELINE_TONE_DOT.normal}`} /> Neutral
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${TIMELINE_TONE_DOT.running}`} /> Active
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${TIMELINE_TONE_DOT.success}`} /> Success
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${TIMELINE_TONE_DOT.failure}`} /> Failed
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${TIMELINE_TONE_DOT.retry}`} /> Retry
        </span>
      </div>

      <div
        key={refreshFlash}
        className={`infra-panel-refresh runtime-event-scroll max-h-[220px] overflow-y-auto rounded-xl border border-stroke/80 bg-zinc-950/50`}
      >
        {loading && !entries.length ? (
          <div className="px-4 py-8 text-sm text-zinc-600">Loading…</div>
        ) : !entries.length ? (
          <div className="px-4 py-8 text-sm text-zinc-600">No events yet</div>
        ) : (
          <ul ref={listRef} className="divide-y divide-stroke/40 font-mono text-[11px] sm:text-xs">
            {entries.map((row, i) => (
              <li
                key={row.id}
                className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2.5 sm:px-4 ${
                  i < 2 ? "runtime-event-fade-in bg-white/[0.015]" : ""
                }`}
              >
                <span className="shrink-0 tabular-nums text-zinc-600">{formatTimelineTs(row.created_at)}</span>
                <span className="inline-flex min-w-0 items-center gap-2">
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${TIMELINE_TONE_DOT[row.tone]} ${
                      row.tone === "running" ? "runtime-pulse-dot" : ""
                    }`}
                    aria-hidden
                  />
                  <span className={`font-medium ${TIMELINE_TONE_CLASS[row.tone]}`}>{row.label}</span>
                </span>
                <span className="text-zinc-700">#{row.job_id}</span>
                {row.trace_id ? (
                  <Link
                    href={`/infra/traces/${encodeURIComponent(row.trace_id)}`}
                    className="truncate text-zinc-600 hover:text-emerald-400/90"
                    title={row.trace_id}
                  >
                    {shortTraceId(row.trace_id)}
                  </Link>
                ) : (
                  <span className="text-zinc-800">—</span>
                )}
                {row.detail ? (
                  <span className="min-w-0 flex-1 truncate text-zinc-700" title={row.detail}>
                    {row.detail}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </ControlPlaneSection>
  );
}
