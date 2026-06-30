"use client";

import type { StudioStatusResponse } from "@/lib/studioApi";
import {
  buildPhotographyWorkflowViews,
  type PhotographyWorkflowNode,
} from "@/lib/studioUi";

type Props = {
  pipeline: StudioStatusResponse["pipeline"];
  events: StudioStatusResponse["events"];
  previewCount: number;
  jobRunning: boolean;
};

function fmtCount(n: number | null | undefined): string {
  if (n == null || n < 0) return "—";
  return n.toLocaleString("en-US");
}

function WorkflowStep({ node, jobRunning }: { node: PhotographyWorkflowNode; jobRunning: boolean }) {
  const { label, count, state } = node;
  const done = state === "done";
  const active = state === "active";
  const failed = state === "failed";

  return (
    <div className="relative flex flex-col gap-1.5 pr-5">
      <p
        className={`text-lg font-medium tabular-nums sm:text-[18px] ${
          done || active ? "text-[#e8e8e8]" : failed ? "text-white/50" : "text-white/35"
        }`}
      >
        {fmtCount(count)}
      </p>
      <p className="text-[11px] uppercase tracking-[0.06em] text-white/30">{label}</p>

      {done ? (
        <span
          className="absolute right-0 top-0 flex h-4 w-4 items-center justify-center rounded-full bg-[rgba(29,158,117,0.2)] text-[9px] text-[#5dcaa5]"
          aria-hidden
        >
          ✓
        </span>
      ) : null}

      {active && jobRunning ? (
        <span
          className="absolute right-0 top-0 h-4 w-4 rounded-full border border-white/30 bg-white/10"
          aria-hidden
        >
          <span className="absolute inset-[3px] animate-pulse rounded-full bg-white/70" />
        </span>
      ) : null}

      {failed ? (
        <span
          className="absolute right-0 top-0 flex h-4 w-4 items-center justify-center rounded-full bg-rose-500/20 text-[9px] text-rose-300"
          aria-hidden
        >
          !
        </span>
      ) : null}
    </div>
  );
}

export function StudioPipelineTimeline({ pipeline, events, previewCount, jobRunning }: Props) {
  const nodes = buildPhotographyWorkflowViews(pipeline, events, previewCount);

  return (
    <section aria-label="Session photo workflow">
      <p className="mb-3 text-[10px] uppercase tracking-[0.1em] text-white/30">
        Workflow — import to delivery
      </p>
      <div className="overflow-x-auto pb-0.5">
        <div className="grid min-w-[min(100%,560px)] grid-cols-2 gap-x-5 gap-y-4 sm:min-w-0 sm:grid-cols-5">
          {nodes.map((node) => (
            <WorkflowStep key={node.label} node={node} jobRunning={jobRunning} />
          ))}
        </div>
      </div>
    </section>
  );
}
