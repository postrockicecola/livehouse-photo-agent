import Link from "next/link";
import { LANDING_AGENT_HOME, LANDING_STUDIO_CTA } from "@/lib/productIa";
import { LandingFooter } from "./LandingFooter";
import { LandingGallerySection } from "./LandingGallerySection";
import { LandingHero } from "./LandingHero";
import { LandingNav } from "./LandingNav";
import { LandingOutcomeSection } from "./LandingOutcomeSection";
import { LandingWorkflowSection } from "./LandingWorkflowSection";

/**
 * Marketing home — one path: outcome → gallery → workflow → try CTA.
 * Infra / Eval live as deeper links (footer + closing CTAs), not equal chapters.
 */
export function LandingPage() {
  return (
    <div className="landing-shell studio-grain relative min-h-screen bg-[var(--luma-bg)] text-[var(--luma-text)]">
      <LandingNav />
      <main>
        <LandingHero />
        <LandingOutcomeSection />
        <LandingGallerySection />
        <LandingWorkflowSection />

        <section className="landing-section border-t border-white/[0.05] py-20 sm:py-28">
          <div className="mx-auto w-full max-w-[104rem] px-5 text-center sm:px-8 lg:px-12">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/28">试用</p>
            <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">
              先看交付结果，再打开作业控制面。
            </h2>
            <p className="mx-auto mt-4 max-w-lg text-sm text-white/38">
              Studio 提交场次，Gallery 确认并用对话 Agent 选片，Infra 查看作业与模型调用。
            </p>
            <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
              <Link
                href={LANDING_AGENT_HOME}
                className="inline-flex rounded-[2px] bg-[rgba(247,244,240,0.94)] px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-[#0a0908] transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(255,244,230,0.35)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--luma-bg)]"
              >
                {LANDING_STUDIO_CTA}
              </Link>
              <Link
                href="/infra?tour=1"
                className="inline-flex rounded-[2px] border border-[var(--luma-matte)] bg-white/[0.03] px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-white/70 transition-colors hover:border-[var(--luma-matte-strong)] hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(255,244,230,0.35)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--luma-bg)]"
              >
                五分钟 walkthrough
              </Link>
              <Link
                href="/eval"
                className="inline-flex rounded-[2px] border border-[var(--luma-stroke)] px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-white/45 transition-colors hover:text-white/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[rgba(255,244,230,0.35)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--luma-bg)]"
              >
                Evaluation
              </Link>
            </div>
          </div>
        </section>
      </main>
      <LandingFooter />
    </div>
  );
}
