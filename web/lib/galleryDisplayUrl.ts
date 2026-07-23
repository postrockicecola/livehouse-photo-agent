import type { GalleryItem } from "@/components/types";
import { filmSourcePathQuotedForItem } from "@/lib/defaultFilmExport";

/** Homepage masonry default look (on-demand ``/api/lab/film-render``, disk-cached server-side). */
export const GALLERY_HOME_FILM_VARIANT = "film_cinestill_800t" as const;

/** 首页胶片缩略图（略小于 Lab，减轻 ``film-render`` 算力）。 */
export const GALLERY_FILM_THUMB_MAX_SIDE = 720;

export const GALLERY_PLAIN_THUMB_MAX_SIDE = 900;

function filmPathQueryValue(pathFragment: string): string {
  const t = pathFragment.trim();
  if (!t) return t;
  try {
    return encodeURIComponent(decodeURIComponent(t));
  } catch {
    return encodeURIComponent(t);
  }
}

function rotateQuery(rotateDeg: number): string {
  const rot = Math.trunc(Number(rotateDeg) || 0);
  return rot ? `&rotate=${rot}` : "";
}

/** Static public assets (Showcase) — skip the image proxy. */
function staticPublicPath(pathOrQuoted: string): string | null {
  const raw = pathOrQuoted.trim();
  if (!raw) return null;
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    /* keep raw */
  }
  if (
    decoded.startsWith("/showcase/") ||
    decoded.startsWith("/demo/") ||
    decoded.startsWith("/brand/")
  ) {
    return decoded;
  }
  return null;
}

/** 原始预览 JPEG（回退用）。 */
export function buildGalleryPlainImageUrl(
  apiBase: string,
  item: GalleryItem,
  maxSide: number = GALLERY_PLAIN_THUMB_MAX_SIDE,
): string | null {
  const pq = item.path_quoted?.trim() || item.path?.trim() || "";
  if (!pq) return null;
  const staticPath = staticPublicPath(pq);
  if (staticPath) return staticPath;
  const path = filmPathQueryValue(pq);
  const rot = rotateQuery(Number(item.rotate_degrees ?? 0));
  return `${apiBase}/image?path=${path}&max_side=${maxSide}${rot}`;
}

/** Cinestill ``film-render``（需显影源路径；无路径时返回 null，由调用方用原图）。 */
export function buildGalleryCinestillRenderUrl(
  apiBase: string,
  item: GalleryItem,
  options?: { maxSide?: number; variant?: string },
): string | null {
  const maxSide = options?.maxSide ?? GALLERY_FILM_THUMB_MAX_SIDE;
  const variant = options?.variant ?? GALLERY_HOME_FILM_VARIANT;
  const pq = filmSourcePathQuotedForItem(item);
  if (!pq) return null;
  const path = filmPathQueryValue(pq);
  const rot = rotateQuery(Number(item.rotate_degrees ?? 0));
  return `${apiBase}/api/lab/film-render?path=${path}&variant=${encodeURIComponent(variant)}&max_side=${maxSide}${rot}`;
}

/** @deprecated 使用 ``buildGalleryPlainImageUrl`` + ``buildGalleryCinestillRenderUrl`` 渐进加载。 */
export function buildGalleryFilmThumbUrl(
  apiBase: string,
  item: GalleryItem,
  options?: { maxSide?: number; variant?: string },
): string | null {
  return (
    buildGalleryCinestillRenderUrl(apiBase, item, options) ??
    buildGalleryPlainImageUrl(apiBase, item, options?.maxSide ?? GALLERY_PLAIN_THUMB_MAX_SIDE)
  );
}
