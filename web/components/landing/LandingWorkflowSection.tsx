"use client";

import { useEffect, useRef, useState } from "react";
import { LANDING_WORKFLOW } from "@/lib/productIa";

function WorkflowStepItem({
  title,
  tagline,
  index,
  visible,
  isLast,
}: {
  title: string;
  tagline: string;
  index: number;
  visible: boolean;
  isLast: boolean;
}) {
  return (
    <li
      className={`landing-workflow-step ${visible ? "landing-workflow-step--visible" : ""}`}
      style={{ transitionDelay: `${index * 70}ms` }}
    >
      <div className="landing-workflow-step-marker" aria-hidden>
        <span className="landing-workflow-step-dot" />
        {!isLast ? <span className="landing-workflow-step-line" /> : null}
      </div>
      <div className="landing-workflow-step-body">
        <h3 className="landing-workflow-step-title">{title}</h3>
        <p className="landing-workflow-step-tagline">{tagline}</p>
      </div>
    </li>
  );
}

export function LandingWorkflowSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const { eyebrow, title, subtitle, steps } = LANDING_WORKFLOW;

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
      { threshold: 0.2 },
    );

    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <section
      ref={sectionRef}
      id="workflow"
      className={`landing-workflow scroll-mt-24 border-t border-white/[0.05] ${visible ? "landing-workflow--visible" : ""}`}
      aria-labelledby="landing-workflow-title"
    >
      <div className="landing-workflow-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <div className="grid gap-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)] lg:gap-20 lg:items-start">
          <header className="lg:sticky lg:top-28">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
            <h2
              id="landing-workflow-title"
              className="landing-workflow-headline mt-4 text-[clamp(2rem,5vw,3.5rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
            >
              {title}
            </h2>
            <p className="mt-5 max-w-md text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>

            <div className="mt-10 hidden flex-wrap gap-2 lg:flex">
              {LANDING_WORKFLOW.phases.map((phase) => (
                <span
                  key={phase.id}
                  className="rounded-full border border-white/[0.08] px-3 py-1 font-mono text-[9px] uppercase tracking-[0.16em] text-white/35"
                >
                  {phase.label}
                </span>
              ))}
            </div>
          </header>

          <ol className="landing-workflow-list" aria-label="Workflow steps">
            {steps.map((step, index) => (
              <WorkflowStepItem
                key={step.id}
                title={step.title}
                tagline={step.tagline}
                index={index}
                visible={visible}
                isLast={index === steps.length - 1}
              />
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
