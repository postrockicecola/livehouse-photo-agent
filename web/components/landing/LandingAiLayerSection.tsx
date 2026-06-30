"use client";

import { useEffect, useRef, useState } from "react";
import { LANDING_AI_LAYER } from "@/lib/productIa";

function FlowStep({
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

export function LandingAiLayerSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const { id, eyebrow, title, subtitle, flow, stages, preview } = LANDING_AI_LAYER;

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
      className={`landing-ai-layer scroll-mt-24 ${visible ? "landing-ai-layer--visible" : ""}`}
      aria-labelledby="landing-ai-layer-title"
    >
      <div className="landing-ai-layer-grid pointer-events-none absolute inset-0 opacity-[0.22]" aria-hidden />
      <div className="landing-ai-layer-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <header className="max-w-3xl">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
          <h2
            id="landing-ai-layer-title"
            className="landing-ai-layer-headline mt-4 text-[clamp(2rem,4.5vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
          >
            {title}
          </h2>
          <p className="mt-5 max-w-2xl text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>
        </header>

        <div className="mt-14 lg:mt-16">
          <p className="mb-5 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">Multimodal output</p>
          <ol className="landing-ai-flow" aria-label="Multimodal pipeline">
            {flow.map((step, index) => (
              <FlowStep
                key={step.id}
                label={step.label}
                tagline={step.tagline}
                index={index}
                visible={visible}
                isLast={index === flow.length - 1}
              />
            ))}
          </ol>
        </div>

        <div className="mt-12 grid gap-8 lg:mt-14 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] lg:gap-12 lg:items-start">
          <div>
            <p className="mb-5 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">Pipeline stages</p>
            <div className="landing-ai-stages">
              {stages.map((stage, index) => (
                <article
                  key={stage.stage}
                  className="landing-ai-stage"
                  style={{ transitionDelay: `${index * 80}ms` }}
                >
                  <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/28">
                    {stage.stage} · {stage.title}
                  </p>
                  <h3 className="landing-ai-stage-name">{stage.name}</h3>
                  <p className="landing-ai-stage-body">{stage.body}</p>
                </article>
              ))}
            </div>
          </div>

          <aside className="landing-ai-preview">
            <p className="mb-4 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">What you see in Gallery</p>
            <div className="landing-ai-preview-score tabular-nums">{preview.score}</div>
            <p className="mt-1 font-mono text-[9px] tabular-nums text-white/32">{preview.dimensions}</p>
            <p className="landing-ai-preview-caption">{preview.caption}</p>
            <ul className="landing-ai-preview-tags">
              {preview.tags.map((tag) => (
                <li key={tag}>#{tag}</li>
              ))}
            </ul>
            <p className="mt-5 font-mono text-[9px] leading-relaxed text-white/24">
              Image → VLM → structured fields your curator can trust.
            </p>
          </aside>
        </div>
      </div>
    </section>
  );
}
