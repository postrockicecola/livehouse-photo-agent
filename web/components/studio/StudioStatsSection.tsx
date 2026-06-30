"use client";

import type { StudioLifetimeStats } from "@/lib/studioApi";
import { formatScaleStat, formatStatSessions } from "@/lib/studioUi";

type Props = {
  stats: StudioLifetimeStats | null;
  loading?: boolean;
  layout?: "row" | "stack";
  variant?: "default" | "glass" | "kpi";
  showHeading?: boolean;
  /** Drop the card border/background (e.g. when rendered over the hero image). */
  bare?: boolean;
};

function KpiCard({
  value,
  label,
  accent,
  bare,
}: {
  value: string;
  label: string;
  accent?: boolean;
  bare?: boolean;
}) {
  return (
    <article className={bare ? "" : "rounded-lg border border-white/[0.06] bg-[#161616] px-4 py-3.5"}>
      <p className={`text-[22px] font-medium tabular-nums tracking-tight ${accent ? "text-[#5dcaa5]" : "text-[#e8e8e8]"}`}>
        {value}
      </p>
      <p className="mt-0.5 text-[11px] text-white/35">{label}</p>
    </article>
  );
}

function StatCard({
  value,
  label,
  variant,
}: {
  value: string;
  label: string;
  variant: "default" | "glass";
}) {
  if (variant === "glass") {
    return (
      <article className="flex flex-col justify-end rounded-xl border border-white/[0.06] bg-white/[0.03] px-5 py-5 backdrop-blur-md sm:px-6 sm:py-6">
        <p className="text-2xl font-light tabular-nums tracking-tight text-white/92 sm:text-3xl md:text-[2rem] md:leading-none">
          {value}
        </p>
        <p className="mt-2 font-mono text-[9px] uppercase tracking-[0.18em] text-white/38 sm:text-[10px]">
          {label}
        </p>
      </article>
    );
  }

  return (
    <article className="flex min-h-[7.5rem] flex-col justify-end rounded-2xl border border-white/[0.08] bg-white/[0.025] px-6 py-6 sm:min-h-[8.5rem] sm:px-8 sm:py-7">
      <p className="text-4xl font-light tabular-nums tracking-tight text-white/95 sm:text-5xl md:text-[3.25rem] md:leading-none">
        {value}
      </p>
      <p className="mt-3 font-mono text-[10px] uppercase tracking-[0.2em] text-white/40 sm:text-[11px]">
        {label}
      </p>
    </article>
  );
}

export function StudioStatsSection({
  stats,
  loading,
  layout = "row",
  variant = "default",
  showHeading = true,
  bare = false,
}: Props) {
  const items: Array<{ value: string; label: string; accent?: boolean }> = [
    {
      value: formatStatSessions(stats?.sessions_total ?? 0, loading),
      label: "Live sessions",
    },
    {
      value: formatScaleStat(stats?.photos_total ?? 0, loading),
      label: "Photos processed",
      accent: true,
    },
    {
      value: formatScaleStat(stats?.exported_photos_total ?? 0, loading),
      label: "Photos exported",
      accent: true,
    },
  ];

  if (variant === "kpi") {
    return (
      <section className="w-full" aria-label="Luma statistics">
        <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-3">
          {items.map((item) => (
            <KpiCard key={item.label} value={item.value} label={item.label} accent={item.accent} bare={bare} />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="luma-stats w-full" aria-label="Luma statistics">
      {showHeading ? (
        <p className="mb-5 font-mono text-[11px] uppercase tracking-[0.22em] text-white/34">Luma Stats</p>
      ) : null}
      <div
        className={
          layout === "stack"
            ? "flex flex-col gap-3"
            : "grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-4"
        }
      >
        {items.map((item) => (
          <StatCard key={item.label} value={item.value} label={item.label} variant={variant === "glass" ? "glass" : "default"} />
        ))}
      </div>
    </section>
  );
}
