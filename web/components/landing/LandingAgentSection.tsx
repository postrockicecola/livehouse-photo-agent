"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LANDING_AGENT } from "@/lib/productIa";
import { getApiBase } from "@/lib/apiBase";
import type { AgentRunSummary } from "@/lib/agentRun";

type LiveMetrics = {
  steps: string;
  inferences: string;
  escalations: string;
  llmRate: string;
  jobId: number | null;
};

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Pick the most informative run: prefer SUCCEEDED with committed loop metrics. */
function pickRun(runs: AgentRunSummary[]): AgentRunSummary | null {
  const withSteps = runs.filter((r) => num((r.metrics ?? {})["steps"]) != null);
  const succeeded = withSteps.filter((r) => r.status === "SUCCEEDED");
  return succeeded[0] ?? withSteps[0] ?? null;
}

function toLiveMetrics(run: AgentRunSummary): LiveMetrics | null {
  const m = run.metrics ?? {};
  const steps = num(m["steps"]);
  if (steps == null) return null;
  const used = num(m["inferences_used"]);
  const max = num(m["max_inferences"]) ?? num(run.max_inferences);
  const esc = num(m["escalations"]) ?? num(run.escalated);
  const rate = num(m["llm_decision_rate"]);
  return {
    steps: String(steps),
    inferences: used != null ? `${used} / ${max ?? "∞"}` : "—",
    escalations: esc != null ? String(esc) : "0",
    llmRate: rate != null ? `${Math.round(rate * 100)}%` : "n/a",
    jobId: num(run.job_id),
  };
}

function LoopStep({
  label,
  tagline,
  index,
  visible,
  isLast,
}: {
  label: string;
  tagline: string;
  index: number;
  visible: boolean;
  isLast: boolean;
}) {
  return (
    <li
      className={`landing-ai-flow-step ${visible ? "landing-ai-flow-step--visible" : ""}`}
      style={{ transitionDelay: `${index * 55}ms` }}
    >
      <div className="landing-ai-flow-node">
        <span className="landing-ai-flow-dot" aria-hidden />
        <div className="landing-ai-flow-card">
          <p className="landing-ai-flow-label">{label}</p>
          <p className="landing-ai-flow-tagline">{tagline}</p>
        </div>
      </div>
      {!isLast ? <span className="landing-ai-flow-connector" aria-hidden /> : null}
    </li>
  );
}

export function LandingAgentSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [live, setLive] = useState<LiveMetrics | null>(null);
  const { id, eyebrow, title, subtitle, loop, highlights, metrics, consoleHref } = LANDING_AGENT;

  useEffect(() => {
    let cancelled = false;
    fetch(`${getApiBase()}/api/infra/agent/runs?limit=12`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { runs?: AgentRunSummary[] } | null) => {
        if (cancelled || !Array.isArray(data?.runs)) return;
        const run = pickRun(data.runs);
        if (run) setLive(toLiveMetrics(run));
      })
      .catch(() => {
        /* fall back to the metric schema below */
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

  return (
    <section
      ref={sectionRef}
      id={id}
      className={`landing-ai-layer scroll-mt-24 ${
        visible ? "landing-ai-layer--visible landing-infra--visible" : ""
      }`}
      aria-labelledby="landing-agent-title"
    >
      <div className="landing-ai-layer-grid pointer-events-none absolute inset-0 opacity-[0.22]" aria-hidden />
      <div className="landing-ai-layer-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <header className="max-w-3xl">
          <p className="font-mono text-[11px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
          <h2
            id="landing-agent-title"
            className="landing-ai-layer-headline mt-4 text-[clamp(2rem,4.5vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
          >
            {title}
          </h2>
          <p className="mt-5 max-w-2xl text-base leading-relaxed text-white/38 sm:text-lg">{subtitle}</p>
        </header>

        <div className="mt-14 lg:mt-16">
          <p className="mb-5 font-mono text-[11px] uppercase tracking-[0.22em] text-white/28">ReAct loop</p>
          <ol className="landing-ai-flow" aria-label="Agent ReAct loop">
            {loop.map((step, index) => (
              <LoopStep
                key={step.id}
                label={step.label}
                tagline={step.tagline}
                index={index}
                visible={visible}
                isLast={index === loop.length - 1}
              />
            ))}
          </ol>
        </div>

        <div className="mt-12 grid gap-8 lg:mt-14 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-12 lg:items-start">
          <div>
            <p className="mb-5 font-mono text-[11px] uppercase tracking-[0.22em] text-white/28">Why it holds up</p>
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
          </div>

          <aside className="landing-ai-preview">
            <div className="mb-4 flex items-baseline justify-between gap-3">
              <p className="font-mono text-[11px] uppercase tracking-[0.22em] text-white/28">
                {live ? "Latest run" : "Every run emits"}
              </p>
              {live ? (
                <span className="font-mono text-[11px] tabular-nums text-emerald-300/70">
                  ● job #{live.jobId ?? "—"}
                </span>
              ) : null}
            </div>

            {live ? (
              <div className="grid grid-cols-2 gap-2.5">
                {[
                  { label: "steps", value: live.steps },
                  { label: "inferences", value: live.inferences },
                  { label: "escalations", value: live.escalations },
                  { label: "llm decision", value: live.llmRate },
                ].map((m) => (
                  <div key={m.label} className="rounded-xl border border-white/[0.07] bg-white/[0.02] px-3 py-2.5">
                    <p className="landing-infra-pillar-value tabular-nums !text-[clamp(1.15rem,2.4vw,1.6rem)]">
                      {m.value}
                    </p>
                    <p className="mt-1 font-mono text-[11px] uppercase tracking-[0.14em] text-white/30">{m.label}</p>
                  </div>
                ))}
              </div>
            ) : (
              <ul className="space-y-3">
                {metrics.map((m) => (
                  <li key={m.key} className="flex items-baseline justify-between gap-3">
                    <span className="font-mono text-[13px] tabular-nums text-white/72">{m.label}</span>
                    <span className="text-[13px] text-white/32">{m.caption}</span>
                  </li>
                ))}
              </ul>
            )}

            <p className="mt-5 font-mono text-[11px] leading-relaxed text-white/24">
              Structured metrics → observability surface for cost & reliability.
            </p>
          </aside>
        </div>

        <Link
          href={consoleHref}
          className="mt-10 inline-flex font-mono text-[12px] uppercase tracking-[0.14em] text-white/30 transition-colors hover:text-white/55"
        >
          See agent runs in infra →
        </Link>
      </div>
    </section>
  );
}
