"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { resolveClientProvenance } from "@/lib/provenance";
import { LANDING_GALLERY_SECTION } from "./landingConfig";
import { LandingGalleryMarquee } from "./LandingGalleryMarquee";
import { LandingGalleryProductMock } from "./LandingGalleryProductMock";
import { buildStudioCoverUrl } from "@/lib/studioUi";

type GalleryImage = {
  path: string;
};

/** Same cover URL rules as Studio session cards (``/showcase/…`` or ``/image?path=``). */
function buildImageUrl(path: string): string {
  return buildStudioCoverUrl(path, 1200) ?? path;
}

export function LandingGallerySection() {
  const { id, eyebrow, title, subtitle } = LANDING_GALLERY_SECTION;
  const [images, setImages] = useState<GalleryImage[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeFeature, setActiveFeature] = useState("score");

  useEffect(() => {
    let cancelled = false;
    fetch("/api/landing/gallery", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { images?: GalleryImage[] } | null) => {
        if (!cancelled) {
          setImages(Array.isArray(data?.images) ? data.images : []);
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section
      id={id}
      className="landing-section landing-gallery-section scroll-mt-24 border-t border-white/[0.05] py-24 sm:py-32"
    >
      <div className="mx-auto w-full max-w-[104rem] px-5 sm:px-8 lg:px-12">
        <header className="max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">{eyebrow}</p>
            <ProvenanceBadge kind={resolveClientProvenance()} />
            <ProvenanceBadge kind="simulated" />
          </div>
          <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-5xl">{title}</h2>
          {subtitle ? <p className="mt-5 max-w-xl text-sm leading-relaxed text-white/38 sm:text-base">{subtitle}</p> : null}
        </header>

        <div className="mt-14 sm:mt-20">
          {loading ? (
            <div className="landing-gallery-product landing-gallery-product--skeleton min-h-[28rem] rounded-2xl border border-white/[0.06] bg-white/[0.02]" />
          ) : images.length > 0 ? (
            <LandingGalleryProductMock
              images={images.slice(0, 4)}
              buildImageUrl={buildImageUrl}
              activeFeature={activeFeature}
              onFeatureHover={setActiveFeature}
            />
          ) : (
            <div className="landing-placeholder min-h-[24rem] rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.015] sm:min-h-[28rem]" />
          )}
        </div>

        <div className="mt-16 flex flex-wrap items-center gap-3 sm:mt-20">
          <Link
            href="/gallery"
            className="inline-flex rounded-full bg-white px-6 py-3 font-mono text-[10px] uppercase tracking-[0.14em] text-[#0a0a0a] transition-opacity hover:opacity-90"
          >
            Open Gallery
          </Link>
          <p className="font-mono text-[10px] text-white/28">只读 Showcase 选片台 · 完整导出请本地启动</p>
        </div>
      </div>

      {!loading && images.length > 0 ? (
        <div className="landing-gallery-stage mt-14 sm:mt-16">
          <LandingGalleryMarquee images={images} buildImageUrl={buildImageUrl} />
        </div>
      ) : null}
    </section>
  );
}
