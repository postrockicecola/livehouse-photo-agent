"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { LANDING_AGENT } from "@/lib/productIa";

export function LandingAgentSection() {
  const sectionRef = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const { id, eyebrow, title, subtitle, honesty, loop, tools, surfaces, guards } = LANDING_AGENT;

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
      className={`landing-agent scroll-mt-24 ${visible ? "landing-agent--visible" : ""}`}
      aria-labelledby="landing-agent-title"
    >
      <div className="landing-agent-glow pointer-events-none absolute inset-0" aria-hidden />

      <div className="relative mx-auto w-full max-w-[104rem] px-5 py-24 sm:px-8 sm:py-32 lg:px-12">
        <header className="max-w-3xl">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
          <h2
            id="landing-agent-title"
            className="landing-agent-headline mt-4 text-[clamp(2rem,4.5vw,3.25rem)] font-light leading-[1.08] tracking-[-0.03em] text-white/[0.92]"
          >
            {title}
          </h2>
          <p className="mt-5 max-w-2xl text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/28">{honesty}</p>
        </header>

        <div className="mt-14 lg:mt-16">
          <p className="mb-5 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">循环</p>
          <ol className="landing-agent-loop" aria-label="Curation agent loop">
            {loop.map((step, index) => (
              <li
                key={step.id}
                className={`landing-agent-loop-step ${visible ? "landing-agent-loop-step--visible" : ""}`}
                style={{ transitionDelay: `${index * 55}ms` }}
              >
                <div className="landing-agent-loop-card">
                  <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/30">
                    {String(index + 1).padStart(2, "0")}
                  </p>
                  <h3 className="mt-2 text-lg font-light tracking-tight text-white/88">{step.label}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-white/38">{step.tagline}</p>
                </div>
                {index < loop.length - 1 ? (
                  <span className="landing-agent-loop-connector" aria-hidden />
                ) : null}
              </li>
            ))}
          </ol>
        </div>

        <div className="mt-12 grid gap-10 lg:mt-14 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:gap-14">
          <div>
            <p className="mb-5 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">Tools</p>
            <ul className="landing-agent-tools">
              {tools.map((tool, index) => (
                <li
                  key={tool.id}
                  className="landing-agent-tool"
                  style={{ transitionDelay: `${index * 50}ms` }}
                >
                  <code className="landing-agent-tool-name">{tool.name}</code>
                  <span className="landing-agent-tool-desc">{tool.description}</span>
                </li>
              ))}
            </ul>
            <p className="mt-5 font-mono text-[9px] uppercase tracking-[0.16em] text-white/24">
              停止条件 · {guards.join(" · ")}
            </p>
          </div>

          <div>
            <p className="mb-5 font-mono text-[9px] uppercase tracking-[0.22em] text-white/28">去哪看</p>
            <div className="space-y-3">
              {surfaces.map((surface, index) => (
                <article
                  key={surface.id}
                  className="landing-agent-surface"
                  style={{ transitionDelay: `${index * 70}ms` }}
                >
                  <h3 className="text-base font-light tracking-tight text-white/85">{surface.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-white/36">{surface.description}</p>
                  <Link
                    href={surface.href}
                    className="mt-4 inline-flex font-mono text-[10px] uppercase tracking-[0.14em] text-white/32 transition-colors hover:text-white/55"
                  >
                    {surface.cta}
                  </Link>
                </article>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
