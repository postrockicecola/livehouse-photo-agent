import type { GalleryExportItem, GalleryItem } from "@/components/types";

/** Default film strip / batch-export grade (must match ``FILM_VARIANT_IDS`` + Lab presets). */
/** Aligns with homepage gallery default (`GALLERY_HOME_FILM_VARIANT`). */
export const DEFAULT_FILM_VARIANT = "film_cinestill_800t" as const;

/** Basename for API / 磁盘查找（``file`` 字段）。 */
export function catalogBasenameForExport(item: GalleryItem): string | null {
  const f = item.file?.trim();
  if (f) return f;
  const p = item.path?.trim();
  if (p) {
    const seg = p.replace(/\\/g, "/").split("/").filter(Boolean);
    const base = seg.pop();
    if (base?.trim()) return base.trim();
  }
  const q = item.path_quoted?.trim();
  if (q) {
    try {
      const decoded = decodeURIComponent(q);
      const seg = decoded.replace(/\\/g, "/").split("/").filter(Boolean);
      const base = seg.pop();
      if (base?.trim()) return base.trim();
    } catch {
      /* ignore */
    }
  }
  return null;
}

function pathFromQuoted(pathQuoted: string): string {
  const t = pathQuoted.trim();
  if (!t) return "";
  try {
    return decodeURIComponent(t).replace(/\\/g, "/");
  } catch {
    return t.replace(/\\/g, "/");
  }
}

/**
 * Stable per-asset id for selection + curation (prefer full path, not basename).
 * Using ``file`` first caused burst / duplicate rows to share one checkbox state across columns.
 */
export function gallerySelectionKey(item: GalleryItem, fallbackIndex?: number): string {
  const path = item.path?.trim().replace(/\\/g, "/");
  if (path) return path;
  const pq = item.path_quoted?.trim();
  if (pq) {
    const decoded = pathFromQuoted(pq);
    if (decoded) return decoded;
  }
  const file = item.file?.trim();
  if (file) return file;
  if (fallbackIndex !== undefined) return `__tile:${fallbackIndex}`;
  return "";
}

/** Absolute FS / static showcase path — must not be joined onto galleryBasePath again. */
export function isAbsoluteMediaPath(p: string): boolean {
  const t = (p || "").trim().replace(/\\/g, "/");
  if (!t) return false;
  if (t.startsWith("/showcase/") || t.startsWith("/demo/")) return true;
  if (t.startsWith("http://") || t.startsWith("https://")) return true;
  return /^(\/|[A-Za-z]:\/)/.test(t);
}

/**
 * Join ``root`` + basename/relative ref without doubling when ``fileOrPath`` is already absolute
 * or already under ``root`` (curation selected_keys are often absolute paths).
 */
export function joinGalleryMediaPath(root: string | null | undefined, fileOrPath: string): string {
  const f = (fileOrPath || "").trim().replace(/\\/g, "/");
  if (!f) return "";
  if (isAbsoluteMediaPath(f)) return f;
  const r = (root || "").trim().replace(/\\/g, "/").replace(/\/$/, "");
  if (!r) return f;
  if (f === r || f.startsWith(`${r}/`)) return f;
  return `${r}/${f.replace(/^\//, "")}`;
}

/** Same source resolution as Lab ``buildStyleEntries`` film rows (before → main → absolute paths). */
export function filmSourcePathQuotedForItem(item: GalleryItem): string | null {
  return (
    item.before_path_quoted?.trim() ||
    item.path_quoted?.trim() ||
    (item.before_path && /^(\/|[A-Za-z]:[\\/])/.test(item.before_path) ? encodeURIComponent(item.before_path) : null) ||
    (item.path && /^(\/|[A-Za-z]:[\\/])/.test(item.path) ? encodeURIComponent(item.path) : null)
  );
}

/** Default「胶片修图」导出规格；无可用源路径时返回 null。 */
export function defaultFilmExportItem(item: GalleryItem): GalleryExportItem | null {
  const file = catalogBasenameForExport(item);
  if (!file) return null;
  const pq = filmSourcePathQuotedForItem(item)?.trim();
  if (!pq) return null;
  return {
    file,
    rotate: Number(item.rotate_degrees ?? 0),
    film_variant: DEFAULT_FILM_VARIANT,
    film_source_path_quoted: pq,
  };
}
