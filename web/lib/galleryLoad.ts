/**
 * Gallery data loading: prefers `/api/gallery/results`, falls back to `/analysis_results.json`
 * (same-origin via Next rewrite) when the API slice is empty so Lab still works if JSON exists.
 */
import { getApiBase } from "@/lib/apiBase";
import type { GalleryItem } from "@/components/types";

export type GalleryLoadSource = "results_api" | "analysis_json" | "none";

export type GallerySort = "overall" | "personalized" | "diverse";

/** First paint: small lite slice (server skips per-row PIL/RAW). */
export const GALLERY_BOOTSTRAP_LIMIT = 36;
/** Infinite scroll / idle prefetch page size. */
export const GALLERY_PAGE_LIMIT = 120;

export type GalleryBootstrap = {
  items: GalleryItem[];
  count: number;
  /** Before burst dedupe (API ``total_raw``). */
  totalRaw: number | null;
  dedupeHidden: number;
  nextOffset: number | null;
  hasMore: boolean;
  activeBaseDir: string | null;
  error: string | null;
  loadSource: GalleryLoadSource;
};

function num(v: unknown, d = 0): number {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string") {
    const x = parseFloat(v);
    return Number.isFinite(x) ? x : d;
  }
  return d;
}

/** Build ``path_quoted`` for ``/image?path=`` when loading raw ``analysis_results.json`` rows in the browser. */
export function normalizeRowToItem(row: unknown, previewsBase: string): GalleryItem | null {
  if (!row || typeof row !== "object") return null;
  const o = row as Record<string, unknown>;
  const file = typeof o.file === "string" ? o.file : undefined;
  let path = typeof o.path === "string" ? o.path : "";
  const base = (previewsBase || "").replace(/\/$/, "");

  if (!path && file && base) {
    path = `${base}/${file}`;
  } else if (path && !path.startsWith("/") && base) {
    path = `${base}/${path.replace(/^\.\//, "")}`;
  }
  if (!path) return null;

  let path_quoted = typeof o.path_quoted === "string" ? o.path_quoted.trim() : "";
  if (!path_quoted) {
    path_quoted = encodeURIComponent(path);
  }

  const item: GalleryItem = {
    file,
    path,
    path_quoted,
  };

  const od = o.rotate_degrees;
  if (typeof od === "number" && od !== 0) item.rotate_degrees = od;
  else if (typeof od === "string" && od.trim()) {
    const r = parseInt(od, 10);
    if (r !== 0 && Number.isFinite(r)) item.rotate_degrees = r;
  }

  if (typeof o.width === "number") item.width = o.width;
  if (typeof o.height === "number") item.height = o.height;

  if (typeof o.orientation === "string") item.orientation = o.orientation;

  item.overall_score = num(o.overall_score ?? (o.scores as Record<string, unknown> | undefined)?.overall, 0);
  item.energy = num(o.energy, 0);
  item.technical = num(o.technical, 0);
  item.composition = num(o.composition, 0);

  if (typeof o.reason === "string" && o.reason.trim()) item.reason = o.reason.trim();
  const rb = o.reason_bilingual;
  if (rb && typeof rb === "object") {
    const rbo = rb as Record<string, unknown>;
    item.reason_bilingual = {
      en: typeof rbo.en === "string" ? rbo.en : undefined,
      zh: typeof rbo.zh === "string" ? rbo.zh : undefined,
    };
  }
  if (Array.isArray(o.tags)) {
    item.tags = o.tags.filter((t): t is string => typeof t === "string" && t.trim().length > 0);
  }
  if (typeof o.category === "string" && o.category.trim()) item.category = o.category.trim();
  if (typeof o.algorithm_version === "string" && o.algorithm_version.trim()) {
    item.algorithm_version = o.algorithm_version.trim();
  }

  return item;
}

async function fetchJson(url: string, signal?: AbortSignal): Promise<any> {
  const res = await fetch(url, { cache: "no-store", signal });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function galleryResultsUrl(
  apiBase: string,
  offset: number,
  limit: number,
  lite = true,
  dedupe = true,
  sort: GallerySort = "overall",
): string {
  const liteQ = lite ? "true" : "false";
  const dedupeQ = dedupe ? "true" : "false";
  return `${apiBase}/api/gallery/results?sort=${encodeURIComponent(sort)}&offset=${offset}&limit=${limit}&lite=${liteQ}&dedupe=${dedupeQ}`;
}

function bootstrapFromResultsPayload(
  data: Record<string, unknown>,
  activeBaseDir: string | null,
): GalleryBootstrap | null {
  const items = (data.items ?? []) as GalleryItem[];
  const countRaw = typeof data.count === "number" ? data.count : items.length;
  const count = Math.max(countRaw, items.length);
  if (items.length === 0 && count === 0) return null;
  const totalRaw =
    typeof data.total_raw === "number" ? data.total_raw : count;
  const dedupeHidden =
    typeof data.dedupe_hidden === "number" ? data.dedupe_hidden : Math.max(0, totalRaw - count);
  return {
    items,
    count,
    totalRaw,
    dedupeHidden,
    nextOffset: (data.next_offset as number | null | undefined) ?? null,
    hasMore: Boolean(data.has_more),
    activeBaseDir,
    error: null,
    loadSource: "results_api",
  };
}

async function bootstrapFromAnalysisJson(
  apiBase: string,
  activeBaseDir: string | null,
  apiError: string | null,
  signal?: AbortSignal,
): Promise<GalleryBootstrap | null> {
  try {
    const raw = await fetchJson(`${apiBase}/analysis_results.json`, signal);
    if (!Array.isArray(raw) || raw.length === 0) return null;
    const base = activeBaseDir ?? "";
    const items = raw
      .map((row) => normalizeRowToItem(row, base))
      .filter((x): x is GalleryItem => Boolean(x?.path_quoted));
    if (items.length === 0) return null;
    items.sort((a, b) => Number(b.overall_score ?? 0) - Number(a.overall_score ?? 0));
    return {
      items,
      count: items.length,
      totalRaw: items.length,
      dedupeHidden: 0,
      nextOffset: null,
      hasMore: false,
      activeBaseDir,
      error: apiError,
      loadSource: "analysis_json",
    };
  } catch {
    return null;
  }
}

/**
 * Initial load: parallel version + lite gallery slice; on failure/abort, try ``analysis_results.json``.
 */
export async function bootstrapGallery(
  signal?: AbortSignal,
  options?: { dedupe?: boolean; sort?: GallerySort },
): Promise<GalleryBootstrap> {
  const dedupe = options?.dedupe !== false;
  const sort: GallerySort =
    options?.sort === "personalized" || options?.sort === "diverse" ? options.sort : "overall";
  const apiBase = getApiBase();
  let activeBaseDir: string | null = null;
  let apiError: string | null = null;
  let aborted = false;

  try {
    const [ver, data] = await Promise.all([
      fetchJson(`${apiBase}/api/debug/version`, signal).catch(() => null),
      fetchJson(galleryResultsUrl(apiBase, 0, GALLERY_BOOTSTRAP_LIMIT, true, dedupe, sort), signal),
    ]);
    if (ver?.active_base_dir) activeBaseDir = String(ver.active_base_dir);
    const boot = bootstrapFromResultsPayload(data, activeBaseDir);
    if (boot) return boot;
  } catch (e: unknown) {
    if ((e as Error)?.name === "AbortError") aborted = true;
    else apiError = e instanceof Error ? e.message : String(e);
  }

  const jsonBoot = await bootstrapFromAnalysisJson(
    apiBase,
    activeBaseDir,
    apiError,
    aborted ? undefined : signal,
  );
  if (jsonBoot) return jsonBoot;

  return {
    items: [],
    count: 0,
    totalRaw: 0,
    dedupeHidden: 0,
    nextOffset: null,
    hasMore: false,
    activeBaseDir,
    error: apiError ?? (aborted ? "请求已取消或超时" : null),
    loadSource: "none",
  };
}

export async function fetchGalleryResultsPage(
  apiBase: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
  dedupe = true,
  sort: GallerySort = "overall",
): Promise<{ items: GalleryItem[]; next_offset: number | null; has_more: boolean; count?: number }> {
  return fetchJson(galleryResultsUrl(apiBase, offset, limit, true, dedupe, sort), signal);
}
