"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  LANDING_BRAIN,
  LANDING_BRAIN_FALLBACK_COUNTS,
  type LandingBrainCounts,
} from "@/lib/productIa";

type TraceItem = {
  id: number;
  job_id: number | null;
  from_status: string;
  to_status: string;
  created_at: number | null;
};

function formatCount(value: number): string {
  return new Intl.NumberFormat("en-US").format(value);
}

function useCountUp(target: number, active: boolean): number {
  const [value, setValue] = useState(0);

  useEffect(() => {
    if (!active) return;
    if (target <= 0) {
      setValue(target);
      return;
    }

    let start: number | null = null;
    let raf = 0;
    const duration = 2000;

    const step = (ts: number) => {
      if (start === null) start = ts;
      const progress = Math.min((ts - start) / duration, 1);
      const eased = 1 - (1 - progress) ** 4;
      setValue(Math.round(target * eased));
      if (progress < 1) raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, active]);

  return value;
}

function EntityCard({
  label,
  caption,
  count,
  active,
}: {
  label: string;
  caption: string;
  count: number;
  active: boolean;
}) {
  const display = useCountUp(count, active);

  return (
    <article className="landing-brain-entity">
      <div className="landing-brain-entity-rule" aria-hidden />
      <p className="landing-brain-entity-count tabular-nums">{formatCount(display)}</p>
      <h3 className="landing-brain-entity-label">{label}</h3>
      <p className="landing-brain-entity-caption">{caption}</p>
    </article>
  );
}

function traceLine(item: TraceItem): string {
  const job = item.job_id != null ? `job #${item.job_id}` : "job";
  const from = item.from_status ? item.from_status : "—";
  const to = item.to_status || "—";
  return `${job} · ${from} → ${to}`;
}

export function LandingBrainSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [counts, setCounts] = useState<LandingBrainCounts>(LANDING_BRAIN_FALLBACK_COUNTS);
  const [trace, setTrace] = useState<TraceItem[]>([]);

  const { id, eyebrow, title, subtitle, manifesto, entities, infraHref } = LANDING_BRAIN;

  useEffect(() => {
    let cancelled = false;
    fetch("/api/landing/brain", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { counts?: LandingBrainCounts; trace?: TraceItem[] } | null) => {
        if (cancelled || !data) return;
        if (data.counts) setCounts({ ...LANDING_BRAIN_FALLBACK_COUNTS, ...data.counts });
        if (Array.isArray(data.trace)) setTrace(data.trace);
      })
      .catch(() => {
        /* fallback counts */
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
          setVisible(true);
          observer.disconnect();
        }
      },
      { threshold: 0.18 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const traceItems = trace.length > 0 ? trace : [
    { id: 1, job_id: 1842, from_status: "QUEUED", to_status: "CLAIMED", created_at: null },
    { id: 2, job_id: 1842, from_status: "CLAIMED", to_status: "INFERENCING", created_at: null },
    { id: 3, job_id: 1842, from_status: "INFERENCING", to_status: "SUCCEEDED", created_at: null },
  ];

  return (
    <section
      ref={sectionRef}
      id={id}
      className={`landing-brain scroll-mt-24 ${visible ? "landing-brain--visible" : ""}`}
      aria-labelledby="landing-brain-title"
    >
      <div className="landing-brain-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <div className="grid gap-14 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)] lg:gap-16 lg:items-start">
          <header className="lg:sticky lg:top-28">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
            <h2
              id="landing-brain-title"
              className="landing-brain-headline mt-4 text-[clamp(2rem,4.8vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
            >
              {title}
            </h2>
            <p className="mt-5 max-w-md text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>

            <div className="landing-brain-manifesto mt-10 space-y-1">
              {manifesto.map((line) => (
                <p key={line} className="text-[clamp(1.05rem,2vw,1.35rem)] font-light tracking-tight text-white/55">
                  {line}
                </p>
              ))}
            </div>

            <Link
              href={infraHref}
              className="mt-10 inline-flex font-mono text-[10px] uppercase tracking-[0.14em] text-white/30 transition-colors hover:text-white/55"
            >
              打开 Brain →
            </Link>
          </header>

          <div>
            <div className="landing-brain-grid">
              {entities.map((entity) => (
                <EntityCard
                  key={entity.id}
                  label={entity.label}
                  caption={entity.caption}
                  count={counts[entity.countKey]}
                  active={visible}
                />
              ))}
            </div>

            <div className="landing-brain-trace mt-8">
              <p className="mb-3 font-mono text-[9px] uppercase tracking-[0.2em] text-white/28">最近状态</p>
              <ul className="landing-brain-trace-list">
                {traceItems.map((item) => (
                  <li key={item.id} className="landing-brain-trace-item">
                    <span className="landing-brain-trace-dot" aria-hidden />
                    <span className="font-mono text-[10px] tracking-wide text-white/42">{traceLine(item)}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
