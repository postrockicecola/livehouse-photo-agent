"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LANDING_AI_LAYER, LANDING_HERO, STUDIO_HOME } from "@/lib/productIa";
import { getApiBase } from "@/lib/apiBase";

type GalleryImage = { path: string };

function buildImageUrl(path: string): string {
  // Bundled demo assets (web/public/demo/*) are served statically by Next.
  if (path.startsWith("/demo/")) return path;
  const apiBase = getApiBase();
  return `${apiBase}/image?path=${encodeURIComponent(path)}&max_side=2000`;
}

export function LandingHero() {
  const { eyebrow, title, subtitle, description, ctaPrimary, ctaSecondary } = LANDING_HERO;
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
    <section
      id="features"
      className="landing-hero relative flex min-h-[100svh] scroll-mt-24 flex-col justify-center overflow-hidden px-4 pb-28 pt-32 sm:px-8 sm:pb-32"
    >
      {/* Glow base — also the fallback when no photo is available */}
      <div className="landing-hero-glow pointer-events-none absolute inset-0" />
      <div className="landing-hero-grid pointer-events-none absolute inset-0 opacity-[0.18]" />
      <div className="landing-hero-arc pointer-events-none absolute inset-0" aria-hidden />

      {/* Full-bleed live photo background */}
      {src ? (
        <>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={src}
            alt="现场照片 · AI 分析示例"
            onLoad={() => setReady(true)}
            className={`pointer-events-none absolute inset-0 h-full w-full object-cover transition-opacity duration-[1200ms] ${
              ready ? "opacity-100" : "opacity-0"
            }`}
          />
          <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]/40" />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-[#0a0a0a]/85 via-[#0a0a0a]/25 to-[#0a0a0a]" />
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-r from-[#0a0a0a]/35 via-transparent to-[#0a0a0a]/35" />
        </>
      ) : null}

      <div className="relative z-10 mx-auto w-full max-w-[42rem] text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.34em] text-white/45">{eyebrow}</p>

        <h1 className="landing-hero-title mt-6 text-[clamp(2.5rem,7vw,4.25rem)] font-light leading-[1.06] tracking-[-0.03em] text-white">
          {title}
        </h1>

        <p className="mt-4 text-[clamp(1rem,2.2vw,1.25rem)] font-light leading-snug tracking-tight text-white/70">
          {subtitle}
        </p>

        <p className="mx-auto mt-7 max-w-md text-[15px] leading-[1.7] text-white/55 sm:text-base sm:leading-relaxed">
          {description}
        </p>

        <div className="mt-11 flex flex-wrap items-center justify-center gap-3 sm:mt-12">
          <Link
            href={STUDIO_HOME}
            className="inline-flex rounded-full bg-white px-8 py-3.5 font-mono text-[10px] uppercase tracking-[0.16em] text-[#0a0a0a] transition-[opacity,transform] duration-300 hover:opacity-90 active:scale-[0.98]"
          >
            {ctaPrimary}
          </Link>
          <a
            href={ctaSecondary.href}
            className="inline-flex rounded-full border border-white/20 bg-white/[0.04] px-8 py-3.5 font-mono text-[10px] uppercase tracking-[0.16em] text-white/70 backdrop-blur-sm transition-colors duration-300 hover:border-white/35 hover:text-white"
          >
            {ctaSecondary.label}
          </a>
        </div>
      </div>

      {/* Subtle AI metadata strip on the photo — not a boxed card */}
      {src && ready ? (
        <div className="absolute inset-x-5 bottom-7 z-10 sm:inset-x-8 lg:inset-x-12">
          <div className="mx-auto flex max-w-[104rem] flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[10px] text-white/45">
            <span className="text-base font-semibold tabular-nums text-white/90">{preview.score}</span>
            <span className="tabular-nums text-white/45">{preview.dimensions}</span>
            <span className="hidden text-white/55 sm:inline">{preview.caption}</span>
            <span className="ml-auto hidden uppercase tracking-[0.18em] text-white/35 sm:inline">AI Analyzed</span>
          </div>
        </div>
      ) : null}
    </section>
  );
}
