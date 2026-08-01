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

function FeaturedFrame({
  frame,
  canGallery,
  priority,
  index,
}: {
  frame: StudioFeaturedFrame;
  canGallery: boolean;
  priority?: boolean;
  index: number;
}) {
  const imgUrl = frameImageUrl(frame.path_quoted);
  const frameNo = String(index + 1).padStart(2, "0");

  const body = (
    <>
      <div className="studio-proof-matte">
        <img
          src={imgUrl}
          alt=""
          decoding="async"
          fetchPriority={priority ? "high" : "auto"}
          loading={priority ? "eager" : "lazy"}
          className="absolute inset-0 h-full w-full object-cover transition-[filter,transform] duration-300 group-hover:brightness-[1.04] group-hover:scale-[1.015]"
        />
      </div>
      <div className="studio-proof-exif">
        <span className="min-w-0 truncate">
          FRAME {frameNo} · {frame.highlight}
        </span>
        <span className="shrink-0 tabular-nums text-[rgba(255,244,230,0.45)]">
          {frame.score_display}
        </span>
      </div>
    </>
  );

  if (canGallery) {
    return (
      <Link
        href="/gallery"
        className="studio-proof-frame group focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[rgba(255,244,230,0.35)]"
      >
        {body}
      </Link>
    );
  }

  return <div className="studio-proof-frame group">{body}</div>;
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
        <p className="mb-3 font-mono text-[10px] uppercase tracking-[0.14em] text-white/30">
          Contact sheet · top frames
        </p>
        <div className="border border-dashed border-[var(--luma-stroke)] px-6 py-10 text-center">
          <p className="text-sm text-white/38">
            Waiting for Previews — once files land, Open gallery works even before VLM finishes.
          </p>
        </div>
      </section>
    );
  }

  if (loading && frames.length === 0) {
    return (
      <section aria-label="Featured frames loading">
        <div className="mb-3 h-3 w-52 animate-pulse rounded-sm bg-white/[0.06]" />
        <div className="lab-film-strip studio-proof-strip">
          <div className="aspect-[3/2] animate-pulse bg-white/[0.04]" />
          <div className="aspect-[3/2] animate-pulse bg-white/[0.04]" />
          <div className="aspect-[3/2] animate-pulse bg-white/[0.04]" />
        </div>
      </section>
    );
  }

  if (frames.length === 0) return null;

  const display = frames.slice(0, 3);

  return (
    <section aria-label="Featured frames">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/30">
          Contact sheet · aesthetic · composition · emotion
        </p>
        {canGallery ? (
          <Link
            href="/gallery"
            className="shrink-0 font-mono text-[10px] uppercase tracking-[0.1em] text-white/22 transition-colors hover:text-white/50"
          >
            Open gallery →
          </Link>
        ) : null}
      </div>

      <div className="lab-film-strip studio-proof-strip">
        <span className="lab-film-strip-edge" aria-hidden>
          Proof
        </span>
        {display.map((frame, i) => (
          <FeaturedFrame
            key={frame.path_quoted}
            frame={frame}
            canGallery={canGallery}
            priority={i === 0}
            index={i}
          />
        ))}
      </div>
    </section>
  );
}
