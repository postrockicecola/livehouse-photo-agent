"use client";

import { useEffect, useState } from "react";
import { LANDING_AI_LAYER, LANDING_HERO } from "@/lib/productIa";
import { getApiBase } from "@/lib/apiBase";
import { LandingHeroPrompt } from "./LandingHeroPrompt";

type GalleryImage = { path: string };

function buildImageUrl(path: string): string {
  if (path.startsWith("/demo/")) return path;
  const apiBase = getApiBase();
  return `${apiBase}/image?path=${encodeURIComponent(path)}&max_side=2000`;
}

export function LandingHero() {
  const { title, subtitle } = LANDING_HERO;
  const { preview } = LANDING_AI_LAYER;

  const [src, setSrc] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/landing/gallery", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { images?: GalleryImage[] } | null) => {
        if (cancelled) return;
        const first = Array.isArray(data?.images) ? data.images[0] : null;
        if (first?.path) setSrc(buildImageUrl(first.path));
      })
      .catch(() => {
        /* fall back to glow hero */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section id="features" className="landing-hero scroll-mt-24" aria-label="首页首屏">
      <div className="landing-hero-glow pointer-events-none absolute inset-0" />
      <div className="landing-hero-grid pointer-events-none absolute inset-0 opacity-[0.12]" />
      <div className="landing-hero-arc pointer-events-none absolute inset-0" aria-hidden />
      <div className="landing-hero-flare pointer-events-none absolute inset-0" aria-hidden />

      {src ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt=""
            onLoad={() => setReady(true)}
            className={`pointer-events-none absolute inset-0 h-full w-full object-cover transition-opacity duration-[1200ms] ${
              ready ? "opacity-100" : "opacity-0"
            }`}
          />
          <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]/72" />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#0a0a0a]/90 via-[#0a0a0a]/55 to-[#0a0a0a]" />
        </>
      ) : null}

      <div className="landing-hero-stage">
        <h1 className="landing-hero-slogan">{title}</h1>
        <p className="mx-auto mt-4 max-w-2xl text-center text-sm leading-relaxed text-white/45 sm:text-base">
          {subtitle}
        </p>
        <LandingHeroPrompt />
      </div>

      {src && ready ? (
        <div className="landing-hero-meta">
          <span className="text-base font-semibold tabular-nums text-white/80">{preview.score}</span>
          <span className="tabular-nums text-white/40">{preview.dimensions}</span>
          <span className="hidden text-white/45 sm:inline">{preview.caption}</span>
        </div>
      ) : null}
    </section>
  );
}
