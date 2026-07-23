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
      {/* Decorative layers sit above the photo; keep subtle so the frame reads as one image. */}
      <div className="landing-hero-glow pointer-events-none absolute inset-0" />
      <div className="landing-hero-grid pointer-events-none absolute inset-0 opacity-[0.12]" />
      <div className="landing-hero-arc pointer-events-none absolute inset-0" aria-hidden />
      <div className="landing-hero-flare pointer-events-none absolute inset-0" aria-hidden />

      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={backgroundSrc}
        alt=""
        fetchPriority="high"
        decoding="async"
        className="pointer-events-none absolute inset-0 h-full w-full object-cover"
      />
      <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]/72" />
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#0a0a0a]/90 via-[#0a0a0a]/55 to-[#0a0a0a]" />

      <div className="landing-hero-stage">
        <h1 className="landing-hero-slogan">{title}</h1>
        <p className="mx-auto mt-4 max-w-2xl text-center text-sm leading-relaxed text-white/45 sm:text-base">
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
