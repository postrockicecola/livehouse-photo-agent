"use client";

import { useEffect, useRef } from "react";
import { fmtTime, formatEventLine } from "./utils";
import type { RuntimeEvent } from "./types";

type Props = {
  events: RuntimeEvent[];
  loading?: boolean;
};

function eventKind(ev: RuntimeEvent): string {
  const msg = (ev.message ?? "").toLowerCase();
  const to = (ev.to_status ?? "").toUpperCase();
  if (msg.includes("retry")) return "retry";
  if (msg.includes("inference") || msg.includes("vlm") || msg.includes("llava")) return "inference";
  if (msg.includes("artifact") || msg.includes("persist")) return "artifact";
  if (msg.includes("latency") || msg.includes("spike")) return "warn";
  if (to === "SUCCEEDED") return "success";
  if (to.includes("FAILED") || to === "DEAD_LETTERED") return "error";
  if (to === "CLAIMED" || msg.includes("claimed")) return "claim";
  return "default";
}

const KIND_CLS: Record<string, string> = {
  retry: "text-amber-300/90",
  inference: "text-violet-300/90",
  artifact: "text-emerald-300/80",
  warn: "text-orange-300/90",
  success: "text-emerald-400/80",
  error: "text-red-300/90",
  claim: "text-sky-300/90",
  default: "text-zinc-400",
};

export function EventStream({ events, loading }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const prevLen = useRef(0);

  useEffect(() => {
    if (events.length > prevLen.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
    prevLen.current = events.length;
  }, [events.length]);

  const visible = events.slice(-40);

  return (
    <section className="overflow-hidden rounded-2xl border border-stroke/80 bg-[#050607]">
      <div className="flex items-center justify-between border-b border-stroke/60 px-4 py-3 sm:px-5">
        <div>
          <h2 className="text-xs uppercase tracking-[0.22em] text-zinc-500">Recent runtime events</h2>
          <p className="mt-0.5 font-mono text-[10px] text-zinc-600">continuous state transitions · job_events stream</p>
        </div>
        <div className="flex items-center gap-2 font-mono text-[10px] text-zinc-600">
          <span className="runtime-pulse-dot h-1.5 w-1.5 rounded-full bg-emerald-400/70" />
          live
        </div>
      </div>

      <div className="runtime-event-scroll max-h-[280px] overflow-y-auto px-4 py-3 font-mono text-[11px] leading-relaxed sm:px-5 sm:text-xs">
        {loading && !events.length ? (
          <div className="py-8 text-zinc-600">awaiting event stream…</div>
        ) : !visible.length ? (
          <div className="py-8 text-zinc-600">no events yet</div>
        ) : (
          <ul className="space-y-1">
            {visible.map((ev, i) => {
              const kind = eventKind(ev);
              const isNew = i >= visible.length - 3;
              return (
                <li
                  key={ev.id}
                  className={`flex gap-3 ${isNew ? "runtime-event-fade-in" : ""}`}
                >
                  <span className="shrink-0 tabular-nums text-zinc-600">{fmtTime(ev.created_at)}</span>
                  <span className={`min-w-0 break-words ${KIND_CLS[kind] ?? KIND_CLS.default}`}>
                    {formatEventLine(ev)}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
