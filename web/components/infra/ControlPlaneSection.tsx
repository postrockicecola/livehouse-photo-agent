"use client";

import type { ReactNode } from "react";

type Props = {
  eyebrow: string;
  title: string;
  subtitle?: string;
  right?: ReactNode;
  children: ReactNode;
  id?: string;
};

export function ControlPlaneSection({ eyebrow, title, subtitle, right, children, id }: Props) {
  return (
    <section id={id} className="glass rounded-2xl border border-stroke p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3 sm:mb-5">
        <div className="min-w-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-zinc-600">{eyebrow}</p>
          <h2 className="mt-0.5 text-lg font-semibold tracking-tight text-zinc-100 sm:text-xl">{title}</h2>
          {subtitle ? (
            <p className="mt-1 max-w-2xl text-[11px] leading-snug text-zinc-600/90">{subtitle}</p>
          ) : null}
        </div>
        {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function LivePulse({ active, intervalSec = 3 }: { active?: boolean; intervalSec?: number }) {
  const on = active !== false;
  return (
    <span className="inline-flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wider text-zinc-600">
      <span
        className={`relative h-2 w-2 rounded-full ${on ? "bg-emerald-400" : "bg-zinc-600"}`}
        aria-hidden
      >
        {on ? <span className="absolute inset-0 animate-ping rounded-full bg-emerald-400/50" /> : null}
      </span>
      Live · {intervalSec}s
    </span>
  );
}
