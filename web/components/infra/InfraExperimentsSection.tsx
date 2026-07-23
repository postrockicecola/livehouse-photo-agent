"use client";

import { useState, type ReactNode } from "react";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";

type Props = {
  children: ReactNode;
};

/**
 * Collapses Agent / RLHF / Prompt (and similar) away from the production main path.
 * Default closed so Infra Console first paints the job + inference spine.
 */
export function InfraExperimentsSection({ children }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <section
      id="infra-experiments"
      className="rounded-2xl border border-dashed border-zinc-700/80 bg-zinc-950/40 p-4 sm:p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-600">
              Infra Experiments
            </p>
            <ProvenanceBadge kind="recorded" />
          </div>
          <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-zinc-200 sm:text-xl">
            Extensions
          </h2>
          <p className="mt-1 max-w-2xl text-[11px] leading-snug text-zinc-600">
            Agent curation, RLHF voting, and prompt A/B — not required for the durable job →
            bounded VLM → ledger main path. Expand only when you want the experimental layer.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          className="shrink-0 rounded-lg border border-zinc-700 bg-zinc-900/80 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-zinc-400 transition-colors hover:border-zinc-500 hover:text-zinc-200"
          aria-expanded={open}
        >
          {open ? "Hide experiments" : "Show experiments"}
        </button>
      </div>
      {open ? <div className="mt-5 space-y-5">{children}</div> : null}
    </section>
  );
}
