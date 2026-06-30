"use client";

import { useEffect, useState } from "react";
import { freshnessLabel, type InfraHealthVerdict } from "@/lib/infraControlPlane";

type Props = {
  verdict: InfraHealthVerdict;
  reason: string;
  lastUpdatedMs: number | null;
  degradedSources: string[];
  loading?: boolean;
};

const VERDICT_META: Record<
  InfraHealthVerdict,
  { label: string; dot: string; text: string; ring: string }
> = {
  operational: {
    label: "Operational",
    dot: "bg-emerald-400",
    text: "text-emerald-200",
    ring: "border-emerald-500/40 bg-emerald-950/20",
  },
  degraded: {
    label: "Degraded",
    dot: "bg-amber-400",
    text: "text-amber-200",
    ring: "border-amber-500/45 bg-amber-950/20",
  },
  down: {
    label: "Down",
    dot: "bg-red-400",
    text: "text-red-200",
    ring: "border-red-500/50 bg-red-950/25 ring-1 ring-red-500/20",
  },
};

const NAV = [
  { href: "/infra/brain", label: "Brain Runtime" },
  { href: "/studio", label: "Studio" },
  { href: "/gallery", label: "Gallery" },
];

export function SystemStatusBar({ verdict, reason, lastUpdatedMs, degradedSources, loading }: Props) {
  const meta = VERDICT_META[verdict];
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const stale = lastUpdatedMs != null && now - lastUpdatedMs > 10_000;

  return (
    <div className="sticky top-0 z-30 -mx-4 mb-5 border-b border-stroke/70 bg-zinc-950/80 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex items-center gap-3">
          <div>
            <div className="font-mono text-[10px] uppercase tracking-[0.24em] text-zinc-500">
              AI Pipeline Control Plane
            </div>
            <h1 className="text-lg font-semibold tracking-tight sm:text-xl">Luma Infra</h1>
          </div>
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold ${meta.ring} ${meta.text}`}
          >
            <span className={`relative h-2 w-2 rounded-full ${meta.dot}`}>
              {verdict !== "operational" ? (
                <span className={`absolute inset-0 animate-ping rounded-full ${meta.dot} opacity-60`} />
              ) : null}
            </span>
            {loading ? "Checking…" : meta.label}
          </span>
          <span className="hidden text-xs text-zinc-500 sm:inline">{reason}</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="text-right">
            <div className="flex items-center gap-1.5 font-mono text-[11px] text-zinc-400">
              <span className={`h-1.5 w-1.5 rounded-full ${stale ? "bg-amber-400" : "bg-emerald-400"}`} />
              {stale ? "stale" : "live"} · {freshnessLabel(lastUpdatedMs, now)}
            </div>
            {degradedSources.length ? (
              <div className="font-mono text-[10px] text-amber-300/80">
                {degradedSources.length} feed(s) degraded
              </div>
            ) : null}
          </div>
          <div className="hidden gap-2 md:flex">
            {NAV.map((n) => (
              <a
                key={n.href}
                href={n.href}
                className="rounded-lg border border-stroke px-3 py-1.5 text-xs text-zinc-300 hover:bg-zinc-800"
              >
                {n.label}
              </a>
            ))}
          </div>
        </div>
      </div>
      {reason ? <div className="mt-1 text-xs text-zinc-500 sm:hidden">{reason}</div> : null}
    </div>
  );
}
