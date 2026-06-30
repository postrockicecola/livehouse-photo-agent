"use client";

import type { ReactNode } from "react";
import type { QueueBacklog } from "./types";

type Props = {
  backlog: QueueBacklog | null;
  loading: boolean;
  error: string | null;
};

function Pill({ children, title: tip }: { children: ReactNode; title?: string }) {
  return (
    <span
      title={tip}
      className="inline-flex max-w-full items-center gap-1 truncate rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-0.5 text-xs text-[#aaaaaa] transition-colors duration-200 ease-out hover:border-white/20 hover:text-[rgba(255,255,255,0.75)]"
    >
      {children}
    </span>
  );
}

export function QueueStatusPills({ backlog, loading, error }: Props) {
  const workers = backlog?.workers?.length ?? 0;
  const t = backlog?.totals;
  const active = t?.active ?? 0;
  const reserved = t?.reserved ?? 0;
  const scheduled = t?.scheduled ?? 0;
  const inFlight = active + reserved + scheduled;
  const llen = t?.redis_list_len;

  const workersTip =
    backlog?.workers?.map((w) => `${w.worker}: A${w.active} R${w.reserved} S${w.scheduled}`).join(" · ") ||
    "无 worker 快照";
  const queueTip = `Active: ${active} · Reserved: ${reserved} · Scheduled: ${scheduled}${llen != null ? ` · Redis LLEN: ${llen}` : ""}`;
  const redisTip = error ? error : "Broker 队列长度可读";

  const queueLabel = loading && !backlog ? "…" : inFlight > 0 ? `busy (${inFlight})` : "idle";

  const redisLabel = loading && !backlog ? "…" : error ? "err" : "OK";

  const workerDot =
    loading && !backlog ? (
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#666666]" aria-hidden />
    ) : workers > 0 ? (
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[rgba(255,255,255,0.45)]" aria-hidden />
    ) : (
      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#666666]" aria-hidden />
    );

  return (
    <div className="flex flex-wrap items-center justify-end gap-1.5 sm:gap-2" aria-label="任务队列状态">
      <Pill title={workersTip}>
        {workerDot}
        <span className="tabular-nums">Workers</span>
        <span className="text-[#666666]">:</span>
        <span className="tabular-nums">{loading && !backlog ? "…" : workers}</span>
      </Pill>
      <Pill title={queueTip}>
        <span>Queue</span>
        <span className="text-[#666666]">:</span>
        <span className={inFlight > 0 ? "text-[rgba(255,255,255,0.75)]" : undefined}>{queueLabel}</span>
      </Pill>
      <Pill title={redisTip}>
        <span>Redis</span>
        <span className="text-[#666666]">:</span>
        <span className={error ? "text-[#aaaaaa]" : undefined}>{redisLabel}</span>
      </Pill>
    </div>
  );
}
