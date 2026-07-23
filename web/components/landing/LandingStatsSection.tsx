"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import {
  LANDING_SCALE_INTRO,
  LANDING_SCALE_STATS,
  type LandingScaleStat,
  type LandingStatKey,
} from "@/lib/productIa";
import { resolveClientProvenance, type ProvenanceKind } from "@/lib/provenance";

type LiveStats = Partial<Record<LandingStatKey, number>>;

function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function useCountUp(target: number, active: boolean, duration = 2200): number {
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!active) return;
    if (target <= 0) {
      setValue(0);
      return;
    }

    let start: number | null = null;
    let raf = 0;

    const step = (ts: number) => {
      if (start === null) start = ts;
      const progress = Math.min((ts - start) / duration, 1);
      const eased = 1 - (1 - progress) ** 4;
      setValue(Math.round(target * eased));
      if (progress < 1) raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, active, duration]);

  return value;
}

function ScaleCell({
  stat,
  active,
  liveValue,
  sectionKind,
}: {
  stat: LandingScaleStat;
  active: boolean;
  liveValue?: number;
  sectionKind: ProvenanceKind;
}) {
  // Live/showcase API value wins; the config `value` is a Recorded Run fallback floor.
  const hasApiValue = typeof liveValue === "number" && Number.isFinite(liveValue);
  const target = hasApiValue ? liveValue : stat.value;
  const display = useCountUp(target, active);
  const kind: ProvenanceKind = hasApiValue ? sectionKind : "recorded";

  return (
    <article className="landing-scale-cell" aria-labelledby={`scale-${stat.id}-value`}>
      <div className="landing-scale-cell-rule" aria-hidden />
      <div className="mb-3">
        <ProvenanceBadge kind={kind} />
      </div>
      <p id={`scale-${stat.id}-value`} className="landing-scale-value tabular-nums">
        {formatCount(display)}
        {!hasApiValue && stat.suffix ? <span className="landing-scale-suffix">{stat.suffix}</span> : null}
      </p>
      <h3 className="landing-scale-label">{stat.label}</h3>
      <p className="landing-scale-caption">{stat.caption}</p>
      {!hasApiValue ? (
        <p className="mt-2 text-[11px] leading-snug text-white/28">
          API 不可达时的历史归档数量级，不是当前 Live 计数。
        </p>
      ) : null}
      {stat.detailHref && stat.detailLabel ? (
        <Link
          href={stat.detailHref}
          className="mt-3 inline-flex font-mono text-[11px] uppercase tracking-[0.12em] text-white/28 transition-colors hover:text-white/52 sm:text-[12px]"
        >
          {stat.detailLabel} →
        </Link>
      ) : null}
    </article>
  );
}

export function LandingStatsSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [active, setActive] = useState(false);
  const [live, setLive] = useState<LiveStats | null>(null);
  const sectionKind = resolveClientProvenance();

  useEffect(() => {
    let cancelled = false;
    fetch("/api/landing/stats", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: LiveStats | null) => {
        if (!cancelled && data) setLive(data);
      })
      .catch(() => {
        /* keep static fallback values — labeled Recorded Run */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const node = sectionRef.current;
    if (!node) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting) {
          setActive(true);
          observer.disconnect();
        }
      },
      { threshold: 0.28 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <section
      ref={sectionRef}
      id="proof"
      className={`landing-scale scroll-mt-24 border-t border-white/[0.04] opacity-90 ${active ? "landing-scale--active" : ""}`}
      aria-labelledby="landing-scale-intro"
    >
      <div className="landing-scale-inner mx-auto w-full max-w-[104rem] px-5 py-16 sm:px-8 sm:py-20 lg:px-12">
        <header className="landing-scale-intro max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[11px] uppercase tracking-[0.32em] text-white/32">
              {LANDING_SCALE_INTRO.eyebrow}
            </p>
            <ProvenanceBadge kind={sectionKind} />
          </div>
          <h2 id="landing-scale-intro" className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">
            {LANDING_SCALE_INTRO.title}
          </h2>
          <p className="mt-4 text-base leading-relaxed text-white/38 sm:text-lg">{LANDING_SCALE_INTRO.subtitle}</p>
        </header>

        <div className="landing-scale-row mt-14 sm:mt-20">
          {LANDING_SCALE_STATS.map((stat) => (
            <ScaleCell
              key={stat.id}
              stat={stat}
              active={active}
              liveValue={live?.[stat.statKey]}
              sectionKind={sectionKind}
            />
          ))}
        </div>
      </div>
    </section>
  );
}
