"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  LANDING_INFRA,
  LANDING_INFRA_FALLBACK_METRICS,
  type LandingInfraMetrics,
} from "@/lib/productIa";

type FlowItem = {
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
    const duration = 1800;

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

function pillarValue(id: string, metrics: LandingInfraMetrics, animated: number): string {
  if (id === "workers") {
    return `${formatCount(metrics.workers_online)} / ${formatCount(metrics.workers_total)}`;
  }
  return formatCount(animated);
}

function PillarCard({
  id,
  label,
  caption,
  metric,
  metrics,
  active,
  delayMs,
}: {
  id: string;
  label: string;
  caption: string;
  metric: number;
  metrics: LandingInfraMetrics;
  active: boolean;
  delayMs: number;
}) {
  const animated = useCountUp(metric, active);

  return (
    <article className="landing-infra-pillar" style={{ transitionDelay: `${delayMs}ms` }}>
      <p className="landing-infra-pillar-value tabular-nums">{pillarValue(id, metrics, animated)}</p>
      <h3 className="landing-infra-pillar-label">{label}</h3>
      <p className="landing-infra-pillar-caption">{caption}</p>
    </article>
  );
}

function flowLine(item: FlowItem): string {
  const job = item.job_id != null ? `job #${item.job_id}` : "job";
  const from = item.from_status || "—";
  const to = item.to_status || "—";
  return `${job} · ${from} → ${to}`;
}

export function LandingInfraSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [metrics, setMetrics] = useState<LandingInfraMetrics>(LANDING_INFRA_FALLBACK_METRICS);
  const [flow, setFlow] = useState<FlowItem[]>([]);

  const { id, eyebrow, title, subtitle, highlights, pillars, consoleHref } = LANDING_INFRA;

  useEffect(() => {
    let cancelled = false;
    fetch("/api/landing/infra", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { metrics?: LandingInfraMetrics; flow?: FlowItem[] } | null) => {
        if (cancelled || !data) return;
        if (data.metrics) setMetrics({ ...LANDING_INFRA_FALLBACK_METRICS, ...data.metrics });
        if (Array.isArray(data.flow)) setFlow(data.flow);
      })
      .catch(() => {
        /* fallback */
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
      { threshold: 0.15 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const flowItems =
    flow.length > 0
      ? flow
      : [
          { id: 1, job_id: 1204, from_status: "FAILED_RETRYABLE", to_status: "QUEUED", created_at: null },
          { id: 2, job_id: 1204, from_status: "QUEUED", to_status: "CLAIMED", created_at: null },
          { id: 3, job_id: 1204, from_status: "INFERENCING", to_status: "SUCCEEDED", created_at: null },
        ];

  const queueFill = Math.min(100, Math.max(8, metrics.queue_depth * 12 + metrics.pipeline_active * 6));

  return (
    <section
      ref={sectionRef}
      id={id}
      className={`landing-infra scroll-mt-24 ${visible ? "landing-infra--visible" : ""}`}
      aria-labelledby="landing-infra-title"
    >
      <div className="landing-infra-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <header className="max-w-3xl">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
          <h2
            id="landing-infra-title"
            className="landing-infra-headline mt-4 text-[clamp(2rem,4.8vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
          >
            {title}
          </h2>
          <p className="mt-5 max-w-2xl text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>
        </header>

        <div className="mt-14 grid gap-10 lg:mt-16 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-14">
          <div className="landing-infra-highlights">
            {highlights.map((item, index) => (
              <article
                key={item.id}
                className="landing-infra-highlight"
                style={{ transitionDelay: `${index * 70}ms` }}
              >
                <h3 className="landing-infra-highlight-title">{item.title}</h3>
                <p className="landing-infra-highlight-desc">{item.description}</p>
              </article>
            ))}
          </div>

          <div className="landing-infra-pillars">
            {pillars.map((pillar, index) => (
              <PillarCard
                key={pillar.id}
                id={pillar.id}
                label={pillar.label}
                caption={pillar.caption}
                metric={metrics[pillar.metricKey]}
                metrics={metrics}
                active={visible}
                delayMs={index * 60}
              />
            ))}
          </div>
        </div>

        <div className="landing-infra-console mt-12 sm:mt-14">
          <div className="landing-infra-console-head">
            <span className="font-mono text-[9px] uppercase tracking-[0.2em] text-white/35">Control plane</span>
            <span className="font-mono text-[9px] tabular-nums text-white/28">
              {formatCount(metrics.pipeline_active)} active · {formatCount(metrics.dead_letter)} dead letter
            </span>
          </div>

          <div className="landing-infra-console-grid">
            <div className="landing-infra-queue">
              <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">Queue pressure</p>
              <div className="landing-infra-queue-track" aria-hidden>
                <span className="landing-infra-queue-fill" style={{ width: `${queueFill}%` }} />
              </div>
              <p className="mt-2 font-mono text-[10px] text-white/38">
                {formatCount(metrics.queue_depth)} queued · scheduling headroom
              </p>
            </div>

            <div className="landing-infra-workers">
              <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">Workers</p>
              <div className="flex flex-wrap gap-2">
                <span className="landing-infra-status-pill is-online">
                  {formatCount(metrics.workers_online)} online
                </span>
                <span className="landing-infra-status-pill">
                  {formatCount(Math.max(0, metrics.workers_total - metrics.workers_online))} standby
                </span>
              </div>
            </div>

            <div className="landing-infra-flow">
              <p className="mb-2 font-mono text-[9px] uppercase tracking-[0.16em] text-white/30">Retry & recovery</p>
              <ul className="landing-infra-flow-list">
                {flowItems.map((item) => (
                  <li key={item.id} className="landing-infra-flow-item">
                    {flowLine(item)}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        <Link
          href={consoleHref}
          className="mt-10 inline-flex font-mono text-[10px] uppercase tracking-[0.14em] text-white/30 transition-colors hover:text-white/55"
        >
          Open infra console →
        </Link>
      </div>
    </section>
  );
}
