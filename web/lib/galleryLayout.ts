"use client";

import { useEffect, useState } from "react";

/**
 * Gallery header + dual-column masonry rail (same max width, centered).
 * ~1520px total → ~760px per column at desktop.
 */
export const GALLERY_MASONRY_MAX_CLASS = "max-w-[1520px]";

/** ≥520px: 2-column waterfall; otherwise single column on narrow phones. */
export function galleryMasonryColumnCount(viewportWidth: number): number {
  return viewportWidth >= 520 ? 2 : 1;
}

export function useGalleryMasonryColumnCount(): number {
  const [n, setN] = useState(2);

  useEffect(() => {
    const q = window.matchMedia("(min-width: 520px)");
    const sync = () => setN(q.matches ? 2 : 1);
    sync();
    q.addEventListener("change", sync);
    return () => q.removeEventListener("change", sync);
  }, []);

  return n;
}

export type MasonrySizeHint = { w?: number; h?: number; orientation?: string };

/** Finite positive aspect weight (height / width). Invalid input → 1. */
export function masonryAspectWeight(hint: MasonrySizeHint | null | undefined): number {
  const w = Number(hint?.w);
  const h = Number(hint?.h);
  if (Number.isFinite(w) && Number.isFinite(h) && w > 0 && h > 0) {
    const ratio = h / w;
    if (Number.isFinite(ratio) && ratio > 0) return ratio;
  }
  const o = String(hint?.orientation ?? "").toLowerCase();
  if (o === "portrait") return 1.35;
  if (o === "landscape") return 0.65;
  return 1;
}

/**
 * Split items into ``n`` columns.
 *
 * Uses only stable hints (API width/height/orientation) — not live image
 * measurements — so lazy-load onLoad cannot reshuffle columns while scrolling.
 * Ties go to the column with fewer items so we never dump everything into col 0.
 */
export function splitIntoNMasonryColumns<T>(
  items: readonly T[],
  n: number,
  hintOf: (item: T, index: number) => MasonrySizeHint | null | undefined,
): { item: T; index: number }[][] {
  const cols = Math.max(1, Math.floor(n) || 1);
  if (cols === 1) return [items.map((item, index) => ({ item, index }))];

  const out: { item: T; index: number }[][] = Array.from({ length: cols }, () => []);
  const heights = new Float64Array(cols);
  const counts = new Int32Array(cols);

  items.forEach((item, index) => {
    const weight = masonryAspectWeight(hintOf(item, index));
    let best = 0;
    for (let i = 1; i < cols; i++) {
      const shorter = heights[i] < heights[best] - 1e-6;
      const tiedFewer = Math.abs(heights[i] - heights[best]) <= 1e-6 && counts[i] < counts[best];
      if (shorter || tiedFewer) best = i;
    }
    out[best].push({ item, index });
    heights[best] += weight;
    counts[best] += 1;
  });

  return out;
}
