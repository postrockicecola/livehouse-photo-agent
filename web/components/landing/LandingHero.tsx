"use client";

import { LANDING_AI_LAYER, LANDING_HERO } from "@/lib/productIa";
import { LandingHeroPrompt } from "./LandingHeroPrompt";

export function LandingHero() {
  const { title, subtitle, backgroundSrc } = LANDING_HERO;
  const { preview } = LANDING_AI_LAYER;

  return (
    <section
      id="features"
      className="landing-hero scroll-mt-24"
      aria-label="首页首屏"
      style={{
        backgroundImage: `url(${backgroundSrc})`,
        backgroundSize: "cover",
        backgroundPosition: "center",
        backgroundColor: "#0a0a0a",
      }}
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={backgroundSrc}
        alt=""
        fetchPriority="high"
        decoding="async"
        className="pointer-events-none absolute inset-0 h-full w-full object-cover"
      />
      <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]/68" />
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#0a0a0a]/85 via-[#0a0a0a]/50 to-[#0a0a0a]" />

      <div className="landing-hero-stage">
        <h1 className="landing-hero-slogan">{title}</h1>
        <p className="mx-auto mt-3 max-w-2xl text-center text-sm leading-relaxed text-white/45 sm:mt-4 sm:text-base">
          {subtitle}
        </p>
        <LandingHeroPrompt />
      </div>

      <div className="landing-hero-meta">
        <span className="text-base font-semibold tabular-nums text-white/80">{preview.score}</span>
        <span className="tabular-nums text-white/40">{preview.dimensions}</span>
        <span className="hidden text-white/45 sm:inline">{preview.caption}</span>
      </div>
    </section>
  );
}
