"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  fetchStudioFeaturedFrames,
  type StudioFeaturedFrame,
} from "@/lib/studioApi";
import { getApiBase } from "@/lib/apiBase";

type Props = {
  previewsDir: string | undefined;
  canGallery: boolean;
};

function frameImageUrl(pathQuoted: string, maxSide = 960): string {
  const base = getApiBase();
  return `${base}/image?path=${pathQuoted}&max_side=${maxSide}`;
}

function FeaturedCard({
  frame,
  canGallery,
  priority,
}: {
  frame: StudioFeaturedFrame;
  canGallery: boolean;
  priority?: boolean;
}) {
  const imgUrl = frameImageUrl(frame.path_quoted);
  const badge = `${frame.highlight} ${frame.score_display}`;

  const inner = (
    <>
      <img
        src={imgUrl}
        alt=""
        decoding="async"
        fetchPriority={priority ? "high" : "auto"}
        loading={priority ? "eager" : "lazy"}
        className="absolute inset-0 h-full w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
      />
      <div className="absolute bottom-[7px] left-2 z-10 rounded border border-white/[0.12] bg-black/60 px-[7px] py-0.5 text-[10px] text-white/80 backdrop-blur-sm">
        {badge}
      </div>
    </>
  );

  const shellClass = "group relative aspect-[3/2] overflow-hidden rounded-[7px] bg-[#161616]";

  if (canGallery) {
    return (
      <Link href="/gallery" className={`${shellClass} block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/30`}>
        {inner}
      </Link>
    );
  }

  return <div className={shellClass}>{inner}</div>;
}

export function StudioFeaturedFrames({ previewsDir, canGallery }: Props) {
  const [frames, setFrames] = useState<StudioFeaturedFrame[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!previewsDir || !canGallery) {
      setFrames([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    (async () => {
      try {
        const data = await fetchStudioFeaturedFrames(previewsDir);
        if (!cancelled) setFrames(data.frames ?? []);
      } catch {
        if (!cancelled) setFrames([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [previewsDir, canGallery]);

  if (!canGallery && !loading) {
    return (
      <section aria-label="Featured frames">
        <p className="mb-3 text-[10px] uppercase tracking-[0.1em] text-white/30">
          Top frames — aesthetic, composition, emotion
        </p>
        <div className="rounded-lg border border-dashed border-white/[0.07] px-6 py-10 text-center">
          <p className="text-sm text-white/38">Run analysis to surface top shots from this session.</p>
        </div>
      </section>
    );
  }

  if (loading && frames.length === 0) {
    return (
      <section aria-label="Featured frames loading">
        <div className="mb-3 h-3 w-48 animate-pulse rounded bg-white/[0.06]" />
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div className="aspect-[3/2] animate-pulse rounded-[7px] bg-white/[0.04]" />
          <div className="aspect-[3/2] animate-pulse rounded-[7px] bg-white/[0.04]" />
          <div className="aspect-[3/2] animate-pulse rounded-[7px] bg-white/[0.04]" />
        </div>
      </section>
    );
  }

  if (frames.length === 0) return null;

  const display = frames.slice(0, 3);

  return (
    <section aria-label="Featured frames">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[10px] uppercase tracking-[0.1em] text-white/30">
          Top frames — aesthetic, composition, emotion
        </p>
        {canGallery ? (
          <Link
            href="/gallery"
            className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-white/20 transition-colors hover:text-white/45"
          >
            Open gallery →
          </Link>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {display.map((frame, i) => (
          <FeaturedCard
            key={frame.path_quoted}
            frame={frame}
            canGallery={canGallery}
            priority={i === 0}
          />
        ))}
      </div>
    </section>
  );
}
