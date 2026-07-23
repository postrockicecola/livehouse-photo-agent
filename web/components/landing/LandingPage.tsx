import Link from "next/link";
import { LANDING_STUDIO_CTA, STUDIO_HOME } from "@/lib/productIa";
import { LandingDocsSection } from "./LandingDocsSection";
import { LandingFooter } from "./LandingFooter";
import { LandingGallerySection } from "./LandingGallerySection";
import { LandingHero } from "./LandingHero";
import { LandingNav } from "./LandingNav";
import { LandingOutcomeSection } from "./LandingOutcomeSection";
import { LandingEvalSection } from "./LandingEvalSection";
import { LandingProductMatrixSection } from "./LandingProductMatrixSection";
import { LandingStatsSection } from "./LandingStatsSection";
import { LandingAiLayerSection } from "./LandingAiLayerSection";
import { LandingBrainSection } from "./LandingBrainSection";
import { LandingInfraSection } from "./LandingInfraSection";
import { LandingWorkflowSection } from "./LandingWorkflowSection";

export function LandingPage() {
  return (
    <div className="landing-shell studio-grain relative min-h-screen bg-[#0a0a0a] text-white">
      <LandingNav />
      <main>
        <LandingHero />
        <LandingOutcomeSection />
        <LandingGallerySection />
        <LandingWorkflowSection />
        <LandingAiLayerSection />
        <LandingInfraSection />
        <LandingEvalSection />
        <LandingBrainSection />
        <LandingProductMatrixSection />
        <LandingStatsSection />
        <LandingDocsSection />

        <section className="landing-section border-t border-white/[0.05] py-20 sm:py-28">
          <div className="mx-auto w-full max-w-[104rem] px-5 text-center sm:px-8 lg:px-12">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/28">试用</p>
            <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">
              先看交付结果，再打开作业控制面。
            </h2>
            <p className="mx-auto mt-4 max-w-md text-sm text-white/38">
              Studio 提交场次，Infra 查看作业与模型调用，Gallery 确认选片。
            </p>
            <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
              <Link
                href="/infra?tour=1"
                className="inline-flex rounded-full bg-white px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-[#0a0a0a] transition-opacity hover:opacity-90"
              >
                五分钟 walkthrough
              </Link>
              <Link
                href={STUDIO_HOME}
                className="inline-flex rounded-full border border-white/20 bg-white/[0.04] px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-white/70 transition-colors hover:border-white/35 hover:text-white"
              >
                {LANDING_STUDIO_CTA}
              </Link>
            </div>
            <p className="mx-auto mt-6 max-w-lg text-[11px] leading-relaxed text-white/28">
              录屏可放在 <code className="text-white/40">web/public/demo/walkthrough.mp4</code>
              ；目前用 Infra Guided Tour 走完同一条路径。
            </p>
          </div>
        </section>
      </main>
      <LandingFooter />
    </div>
  );
}
