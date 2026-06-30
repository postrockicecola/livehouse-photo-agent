import Link from "next/link";
import { LANDING_STUDIO_CTA, STUDIO_HOME } from "@/lib/productIa";
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
        <LandingBrainSection />
        <LandingInfraSection />
        <LandingGallerySection />
        <LandingDocsSection />
        <LandingProductMatrixSection />

        <section className="landing-section border-t border-white/[0.05] py-20 sm:py-28">
          <div className="mx-auto w-full max-w-[104rem] px-5 text-center sm:px-8 lg:px-12">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/28">Try it</p>
            <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">
              想知道它怎么跑？直接进去看。
            </h2>
            <p className="mx-auto mt-4 max-w-md text-sm text-white/38">
              进入 Studio 浏览真实场次与 pipeline 产物，到 Gallery 体验选片与导出。
            </p>
            <div className="mt-10 flex flex-wrap items-center justify-center gap-3">
              <Link
                href={STUDIO_HOME}
                className="inline-flex rounded-full bg-white px-6 py-3 font-mono text-[10px] uppercase tracking-[0.16em] text-[#0a0a0a] transition-opacity hover:opacity-90"
              >
                {LANDING_STUDIO_CTA}
              </Link>
            </div>
          </div>
        </section>
      </main>
      <LandingFooter />
    </div>
  );
}
