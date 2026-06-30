import type { GalleryExportItem, GalleryItem } from "@/components/types";
import {
  catalogBasenameForExport,
  DEFAULT_FILM_VARIANT,
  defaultFilmExportItem,
  filmSourcePathQuotedForItem,
} from "@/lib/defaultFilmExport";

const FILM_VARIANT_LABELS: Record<string, string> = {
  film_automated: "Automated · AI 智能修图",
  film_livehouse: "Film · Livehouse",
  film_cinestill_800t: "Film · Cinestill 800T",
  film_cinestill_50d: "Film · Cinestill 50D",
  film_portra_400: "Film · Portra 400",
  film_gold_200: "Film · Gold 200",
  film_ektar_100: "Film · Ektar 100",
  film_velvia_50: "Film · Velvia 50",
  film_superia_400: "Film · Superia 400",
  film_kodachrome_64: "Film · Kodachrome 64",
  film_lomo_xpro: "Film · Lomo X-Pro",
  film_ultra_vivid: "Film · Ultra Vivid",
  film_neon_pop: "Film · Neon Pop",
  film_neon_tokyo: "Film · Neon Tokyo",
  film_neon_cyan: "Film · Neon Cyan",
  film_neon_magenta: "Film · Neon Magenta",
  film_neon_club: "Film · Neon Club",
  film_neon_signage: "Film · Neon Signage",
  film_neon_haze: "Film · Neon Haze",
  film_teal_magenta: "Film · Teal & Magenta",
  film_sunset_chrome: "Film · Sunset Chrome",
  film_agfa_vista_200: "Film · Agfa Vista 200",
  film_astia_100f: "Film · Astia 100F",
  film_polaroid_vivid: "Film · Polaroid Vivid",
  film_holga_vivid: "Film · Holga Vivid",
  film_provia_100f: "Film · Provia 100F",
  film_dutch_golden: "Film · Dutch Golden",
  film_aquamarine_pop: "Film · Aquamarine Pop",
  film_rose_gold: "Film · Rose Gold",
  film_expired_slide: "Film · Expired Slide",
  film_candy_chrome: "Film · Candy Chrome",
  film_fuji_400h: "Film · Fuji 400H",
  film_fuji_classic_neg: "Film · Fuji Classic Neg",
  film_mexico_sun: "Film · Mexico Sun",
  film_spain_passion: "Film · Spain Passion",
  film_latin_cinema: "Film · Latin Cinema",
  film_wong_kar_wai: "Film · Wong Kar-wai",
  film_retro_literary_portrait: "Film · Retro Literary Portrait",
  film_cold_v2: "Film · Warm Orange",
  film_cold_v3: "Film · Cold v3",
  film_cold_v4: "Film · Cinema",
  film_black_mist: "Film · Black Mist",
  film_ricoh_gr: "Film · Ricoh GR (Positive)",
  film_hp5_bw: "Film · Ilford HP5",
  film_tri_x_bw: "Film · Kodak Tri-X",
};

function filmPathQueryValue(pathFragment: string): string {
  const t = pathFragment.trim();
  if (!t) return t;
  try {
    return encodeURIComponent(decodeURIComponent(t));
  } catch {
    return encodeURIComponent(t);
  }
}

function buildImageUrl(apiBase: string, pathQuoted: string, maxSide: number, rotateDeg = 0) {
  const path = filmPathQueryValue(pathQuoted);
  const rot = rotateDeg ? `&rotate=${rotateDeg}` : "";
  return `${apiBase}/image?path=${path}&max_side=${maxSide}${rot}`;
}

function buildFilmRenderUrl(
  apiBase: string,
  sourcePathQuoted: string,
  variant: string,
  maxSide: number,
  rotateDeg = 0,
  adjustPayload: Record<string, number> | null = null,
) {
  const path = filmPathQueryValue(sourcePathQuoted);
  const rot = rotateDeg ? `&rotate=${rotateDeg}` : "";
  const adj =
    adjustPayload && Object.keys(adjustPayload).length > 0
      ? `&adjust=${encodeURIComponent(JSON.stringify(adjustPayload))}`
      : "";
  return `${apiBase}/api/lab/film-render?path=${path}&variant=${encodeURIComponent(variant)}&max_side=${maxSide}${rot}${adj}`;
}

/** 与批量导出一致的有效规格（含默认胶片 / 会话 Vibe 覆盖默认 livehouse）。 */
export function resolvePreviewExportSpec(
  item: GalleryItem,
  stored: GalleryExportItem | undefined,
  options?: { sessionFilmVariant?: string | null; useSessionVibe?: boolean },
): GalleryExportItem | null {
  const file = catalogBasenameForExport(item);
  if (!file) return null;
  const rotate = Number(item.rotate_degrees ?? 0);
  const def = defaultFilmExportItem(item);

  let spec: GalleryExportItem = stored
    ? { ...stored, file, rotate }
    : def ?? { file, rotate };

  const alt = (spec.alternate_jpeg_path_quoted ?? "").trim();
  const fv = (spec.film_variant ?? "").trim();
  if (!fv && !alt && def) spec = { ...def, file, rotate };

  if (options?.useSessionVibe && options.sessionFilmVariant?.trim() && !alt) {
    const sv = options.sessionFilmVariant.trim();
    const currentFv = (spec.film_variant ?? "").trim();
    if (!currentFv || currentFv === DEFAULT_FILM_VARIANT) {
      const pq = filmSourcePathQuotedForItem(item);
      if (pq) {
        spec = { file, rotate, film_variant: sv, film_source_path_quoted: pq };
      }
    }
  }

  if (!(spec.film_variant ?? "").trim() && !(spec.alternate_jpeg_path_quoted ?? "").trim() && def) {
    return { ...def, file, rotate };
  }
  return spec;
}

export function exportPreviewLabel(spec: GalleryExportItem): string {
  const fv = (spec.film_variant ?? "").trim();
  if (fv) return FILM_VARIANT_LABELS[fv] ?? fv;
  if ((spec.alternate_jpeg_path_quoted ?? "").trim()) return "Alternate JPEG";
  return "默认预览";
}

export function buildExportPreviewUrl(
  apiBase: string,
  spec: GalleryExportItem,
  maxSide: number,
): string | null {
  const rotate = Number(spec.rotate ?? 0);
  const fv = (spec.film_variant ?? "").trim();
  if (fv) {
    const pq = (spec.film_source_path_quoted ?? "").trim();
    if (!pq) return null;
    const adj = fv === "film_automated" ? spec.automated_adjust ?? null : null;
    return buildFilmRenderUrl(apiBase, pq, fv, maxSide, rotate, adj);
  }
  const alt = (spec.alternate_jpeg_path_quoted ?? "").trim();
  if (alt) return buildImageUrl(apiBase, alt, maxSide, rotate);
  return null;
}
