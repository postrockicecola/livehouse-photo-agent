"use client";

import type { StudioLifetimeStats } from "@/lib/studioApi";
import { STUDIO_SHELL_INNER, STUDIO_SHELL_BLEED_X } from "@/lib/studioLayout";
import { formatScaleStat, formatShowcaseSessions } from "@/lib/studioUi";

type Props = {
  stats: StudioLifetimeStats | null;
  loading?: boolean;
};

const TAGLINE = "From thousands of frames,\nto the moments worth keeping.";

function ShowcaseMetric({
  value,
  label,
  loading,
}: {
  value: string;
  label: string;
  loading?: boolean;
}) {
  return (
    <div className="flex flex-col items-center text-center sm:items-start sm:text-left">
      <p
        className={`text-[clamp(2.25rem,7vw,4.75rem)] font-light leading-[0.95] tracking-[-0.04em] text-white ${
          loading ? "animate-pulse text-white/30" : ""
        }`}
      >
        {value}
      </p>
      <p className="mt-3 max-w-[12rem] font-mono text-[10px] uppercase leading-relaxed tracking-[0.22em] text-white/42 sm:text-[11px]">
        {label}
      </p>
    </div>
  );
}

export function StudioShowcaseSection({ stats, loading }: Props) {
  const photos = formatScaleStat(stats?.photos_total ?? 0, loading);
  const sessions = formatShowcaseSessions(stats?.sessions_total ?? 0, loading);
  const delivered = formatScaleStat(stats?.exported_photos_total ?? 0, loading);

  return (
    <section
      className={`relative overflow-hidden border-y border-white/[0.06] py-12 sm:py-16 md:py-20 lg:py-24 ${STUDIO_SHELL_BLEED_X}`}
      aria-label="Luma at scale"
    >
      <div className={`relative ${STUDIO_SHELL_INNER}`}>
        <p className="mx-auto max-w-3xl whitespace-pre-line text-center text-[clamp(1.35rem,3.2vw,2.35rem)] font-light leading-[1.25] tracking-[-0.02em] text-white/88 sm:leading-[1.2]">
          {TAGLINE}
        </p>

        <div className="mt-14 grid grid-cols-1 gap-12 sm:mt-16 sm:grid-cols-3 sm:gap-10 md:mt-20 md:gap-14 lg:gap-16">
          <ShowcaseMetric value={photos} label="Photos Processed" loading={loading} />
          <ShowcaseMetric value={sessions} label="Live Sessions" loading={loading} />
          <ShowcaseMetric value={delivered} label="Photos Delivered" loading={loading} />
        </div>
      </div>
    </section>
  );
}
