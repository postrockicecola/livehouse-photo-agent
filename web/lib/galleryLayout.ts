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
