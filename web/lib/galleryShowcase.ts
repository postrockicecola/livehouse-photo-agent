/**
 * Read-only Showcase Gallery helpers (no FastAPI / analysis_results on disk).
 */
import type { GalleryItem } from "@/components/types";

export type GalleryShowcaseFixture = {
  session_date?: string;
  session_key?: string;
  band_name?: string;
  active_base_dir?: string;
  count?: number;
  items: GalleryItem[];
};

export function paginateGalleryShowcase(
  fixture: GalleryShowcaseFixture,
  opts: { offset?: number; limit?: number; sort?: string },
) {
  const offset = Math.max(0, Math.floor(opts.offset ?? 0));
  const limit = Math.min(5000, Math.max(1, Math.floor(opts.limit ?? 120)));
  const sort = String(opts.sort || "overall");

  let items = [...(fixture.items ?? [])];
  if (sort !== "diverse") {
    items.sort((a, b) => Number(b.overall_score ?? 0) - Number(a.overall_score ?? 0));
  }

  const total = items.length;
  const slice = items.slice(offset, offset + limit);
  const end = offset + slice.length;
  const hasMore = end < total;

  return {
    count: total,
    total_raw: total,
    dedupe_hidden: 0,
    dedupe_enabled: true,
    grouped: sort === "diverse",
    sort,
    taste_personalized: false,
    offset,
    limit,
    next_offset: hasMore ? end : null,
    has_more: hasMore,
    items: slice,
    film_prewarm_task_id: null,
    showcase: true,
    session_date: fixture.session_date ?? null,
    session_key: fixture.session_key ?? null,
    band_name: fixture.band_name ?? null,
  };
}
