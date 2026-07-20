import Link from "next/link";
import { LANDING_STUDIO_CTA, STUDIO_HOME } from "@/lib/productIa";
import { LandingAgentSection } from "./LandingAgentSection";
import { LandingDocsSection } from "./LandingDocsSection";
import { LandingFooter } from "./LandingFooter";
import { LandingGallerySection } from "./LandingGallerySection";
import { LandingHero } from "./LandingHero";
import { LandingNav } from "./LandingNav";
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
        <LandingStatsSection />
        <LandingWorkflowSection />
        <LandingAiLayerSection />
        <LandingInfraSection />
        <LandingAgentSection />
        <LandingBrainSection />
        <LandingGallerySection />
        <LandingDocsSection />
        <LandingProductMatrixSection />

        <section className="landing-section border-t border-white/[0.05] py-20 sm:py-28">
          <div className="mx-auto w-full max-w-[104rem] px-5 text-center sm:px-8 lg:px-12">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/28">试用</p>
            <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">
              进系统看推理和 Agent 怎么跑。
            </h2>
            <p className="mx-auto mt-4 max-w-md text-sm text-white/38">
              Studio 看场次和 pipeline；Infra 看队列和 Agent step；Gallery 里可以用 ChatDock。
            </p>
            <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
              <Link
                href={STUDIO_HOME}
                className="inline-flex rounded-full bg-white px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-[#0a0a0a] transition-opacity hover:opacity-90"
              >
                {LANDING_STUDIO_CTA}
              </Link>
              <Link
                href="/infra"
                className="inline-flex rounded-full border border-white/20 bg-white/[0.04] px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-white/70 transition-colors hover:border-white/35 hover:text-white"
              >
                打开 Infra
              </Link>
            </div>
          </div>
        </section>
      </main>
      <LandingFooter />
    </div>
  );
}
