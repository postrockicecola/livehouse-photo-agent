"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { catalogBasenameForExport, DEFAULT_FILM_VARIANT } from "@/lib/defaultFilmExport";
import { isFilmPreviewWarmed, preloadFilmPreviewUrl, preloadFilmPreviewUrls } from "@/lib/filmPreviewPreload";
import {
  CURATION_LIKE_REASON_LABELS,
  CURATION_LIKE_REASONS,
  type CurationLikeReason,
} from "@/lib/galleryCuration";
import type { GalleryExportItem, GalleryItem } from "./types";
import { OpticalConsolePanel } from "./OpticalConsolePanel";
import {
  OPTICAL_NEUTRAL,
  opticalCacheKey,
  opticalStateToApiPayload,
  type OpticalConsoleState,
} from "@/lib/opticalConsole";

type Props = {
  item: GalleryItem | null;
  apiBase: string;
  onClose: () => void;
  /** When set, strip thumbnails show「选此效果」and highlight the row matching this spec. */
  chosenExport?: GalleryExportItem | null;
  onPickExportStyle?: (spec: GalleryExportItem, label: string) => void;
  /** Homepage session vibe: default strip selection when opening Lab. */
  sessionFilmVariant?: string | null;
  /** Same key as grid selection / ``gallery_curation`` feedback. */
  selectionKey?: string;
  isSelected?: boolean;
  likeReasons?: CurationLikeReason[];
  onToggleSelection?: (checked: boolean) => void;
  onToggleLikeReason?: (reason: CurationLikeReason) => void;
};

/** ``op_kernel`` film looks — rendered on demand via ``GET /api/lab/film-render`` (must match backend ``FILM_VARIANT_IDS``). */
const FILM_STYLE_PRESETS = [
  { id: "film_livehouse", label: "Film · Livehouse" },
  { id: "film_cinestill_800t", label: "Film · Cinestill 800T" },
  { id: "film_cinestill_50d", label: "Film · Cinestill 50D" },
  { id: "film_portra_400", label: "Film · Portra 400" },
  { id: "film_gold_200", label: "Film · Gold 200" },
  { id: "film_ektar_100", label: "Film · Ektar 100" },
  { id: "film_velvia_50", label: "Film · Velvia 50" },
  { id: "film_superia_400", label: "Film · Superia 400" },
  { id: "film_kodachrome_64", label: "Film · Kodachrome 64" },
  { id: "film_lomo_xpro", label: "Film · Lomo X-Pro" },
  { id: "film_ultra_vivid", label: "Film · Ultra Vivid" },
  { id: "film_neon_pop", label: "Film · Neon Pop" },
  { id: "film_neon_tokyo", label: "Film · Neon Tokyo" },
  { id: "film_neon_cyan", label: "Film · Neon Cyan" },
  { id: "film_neon_magenta", label: "Film · Neon Magenta" },
  { id: "film_neon_club", label: "Film · Neon Club" },
  { id: "film_neon_signage", label: "Film · Neon Signage" },
  { id: "film_neon_haze", label: "Film · Neon Haze" },
  { id: "film_teal_magenta", label: "Film · Teal & Magenta" },
  { id: "film_sunset_chrome", label: "Film · Sunset Chrome" },
  { id: "film_agfa_vista_200", label: "Film · Agfa Vista 200" },
  { id: "film_astia_100f", label: "Film · Astia 100F" },
  { id: "film_polaroid_vivid", label: "Film · Polaroid Vivid" },
  { id: "film_holga_vivid", label: "Film · Holga Vivid" },
  { id: "film_provia_100f", label: "Film · Provia 100F" },
  { id: "film_dutch_golden", label: "Film · Dutch Golden" },
  { id: "film_aquamarine_pop", label: "Film · Aquamarine Pop" },
  { id: "film_rose_gold", label: "Film · Rose Gold" },
  { id: "film_expired_slide", label: "Film · Expired Slide" },
  { id: "film_candy_chrome", label: "Film · Candy Chrome" },
  { id: "film_fuji_400h", label: "Film · Fuji 400H" },
  { id: "film_fuji_classic_neg", label: "Film · Fuji Classic Neg" },
  { id: "film_mexico_sun", label: "Film · Mexico Sun" },
  { id: "film_spain_passion", label: "Film · Spain Passion" },
  { id: "film_latin_cinema", label: "Film · Latin Cinema" },
  { id: "film_wong_kar_wai", label: "Film · Wong Kar-wai" },
  { id: "film_retro_literary_portrait", label: "Film · Retro Literary Portrait" },
  { id: "film_cold_v2", label: "Film · Warm Orange" },
  { id: "film_cold_v3", label: "Film · Cold v3" },
  { id: "film_cold_v4", label: "Film · Cinema" },
  { id: "film_black_mist", label: "Film · Black Mist" },
  { id: "film_ricoh_gr", label: "Film · Ricoh GR (Positive)" },
  { id: "film_hp5_bw", label: "Film · Ilford HP5" },
  { id: "film_tri_x_bw", label: "Film · Kodak Tri-X" },
] as const;

/** Non-film slots (paths only when API JSON includes ``style_variants``). */
const OTHER_STYLE_PRESETS = [
  { id: "leica", label: "Leica" },
  { id: "warm", label: "Warm" },
  { id: "cool", label: "Cool" },
  { id: "bw", label: "B&W" },
] as const;

/** Backend variant id for the VLM-driven per-image grade ("Automated"). */
const AUTOMATED_VARIANT_ID = "film_automated";

function adjustmentsActive(adj: Record<string, number> | null | undefined): boolean {
  if (!adj) return false;
  return Object.values(adj).some((v) => typeof v === "number" && Math.abs(v) > 1e-3);
}

type StyleStripEntry = {
  id: string;
  label: string;
  /** Image path query fragment (already ``encodeURIComponent``-safe from gallery API). */
  pathQuoted: string | null;
  /** When set, URL uses ``/api/lab/film-render`` with ``path=pathQuoted``. */
  filmVariant?: string;
  /** Numeric grade params for ``film_automated`` (sent as ``&adjust=<json>``). */
  adjust?: Record<string, number> | null;
  score?: number;
};

function buildStyleEntries(item: GalleryItem): StyleStripEntry[] {
  const originalQ = item.before_path_quoted?.trim() || null;
  const mainQ = item.path_quoted?.trim() || null;
  const api = item.style_variants ?? [];

  const rows: StyleStripEntry[] = [];

  if (originalQ) {
    rows.push({ id: "original", label: "Original", pathQuoted: originalQ });
  }

  if (!originalQ && mainQ) {
    rows.push({ id: "original", label: "Original", pathQuoted: mainQ, score: item.overall_score });
  }

  const filmSourceQuoted =
    item.before_path_quoted?.trim() ||
    item.path_quoted?.trim() ||
    (item.before_path && /^(\/|[A-Za-z]:[\\/])/.test(item.before_path) ? encodeURIComponent(item.before_path) : null) ||
    (item.path && /^(\/|[A-Za-z]:[\\/])/.test(item.path) ? encodeURIComponent(item.path) : null);

  if (filmSourceQuoted && adjustmentsActive(item.editing_adjustments)) {
    rows.push({
      id: AUTOMATED_VARIANT_ID,
      label: "Automated · AI 智能修图",
      pathQuoted: filmSourceQuoted,
      filmVariant: AUTOMATED_VARIANT_ID,
      adjust: item.editing_adjustments ?? null,
    });
  }

  for (const p of FILM_STYLE_PRESETS) {
    rows.push({
      id: p.id,
      label: p.label,
      pathQuoted: filmSourceQuoted,
      filmVariant: p.id,
    });
  }

  for (const p of OTHER_STYLE_PRESETS) {
    const hit = api.find((v) => v.id === p.id);
    rows.push({
      id: p.id,
      label: hit?.label ?? p.label,
      pathQuoted: hit?.path_quoted?.trim() || null,
      score: hit?.score,
    });
  }

  return rows;
}

function pathsEqualForExport(a: string | null | undefined, b: string | null | undefined): boolean {
  const x = (a ?? "").trim();
  const y = (b ?? "").trim();
  if (!x && !y) return true;
  if (!x || !y) return false;
  try {
    return decodeURIComponent(x) === decodeURIComponent(y);
  } catch {
    return x === y;
  }
}

export function styleEntryToGalleryExportItem(item: GalleryItem, entry: StyleStripEntry): GalleryExportItem | null {
  const catalogFile = catalogBasenameForExport(item);
  if (!catalogFile || !stripEntryEnabled(entry)) return null;
  const rotate = Number(item.rotate_degrees ?? 0);
  if (entry.filmVariant) {
    if (!entry.pathQuoted?.trim()) return null;
    return {
      file: catalogFile,
      rotate,
      film_variant: entry.filmVariant,
      film_source_path_quoted: entry.pathQuoted,
      ...(entry.filmVariant === AUTOMATED_VARIANT_ID && entry.adjust
        ? { automated_adjust: entry.adjust }
        : {}),
    };
  }
  if (!entry.pathQuoted?.trim()) return null;
  return { file: catalogFile, rotate, alternate_jpeg_path_quoted: entry.pathQuoted };
}

function entryMatchesChosenExport(entry: StyleStripEntry, chosen: GalleryExportItem | null | undefined): boolean {
  if (!chosen?.file) return false;
  if (chosen.film_variant) {
    return (
      Boolean(entry.filmVariant) &&
      entry.filmVariant === chosen.film_variant &&
      pathsEqualForExport(entry.pathQuoted, chosen.film_source_path_quoted)
    );
  }
  if ((chosen.alternate_jpeg_path_quoted || "").trim()) {
    return (
      !entry.filmVariant &&
      pathsEqualForExport(entry.pathQuoted, chosen.alternate_jpeg_path_quoted)
    );
  }
  return false;
}

function stripEntryEnabled(e: StyleStripEntry): boolean {
  return Boolean(e.pathQuoted?.trim());
}

/** 主预览初始选中：Cinestill 800T → 会话 vibe → 其余胶片（不含原图默认）。 */
function pickDefaultSelectedId(entries: StyleStripEntry[], sessionFilmVariant?: string | null): string {
  const cine = entries.find((e) => e.id === "film_cinestill_800t" && stripEntryEnabled(e));
  if (cine) return cine.id;
  const primary = entries.find((e) => e.id === DEFAULT_FILM_VARIANT && stripEntryEnabled(e));
  if (primary) return primary.id;
  const sv = (sessionFilmVariant ?? "").trim();
  if (sv) {
    const sessionHit = entries.find((e) => e.id === sv && stripEntryEnabled(e));
    if (sessionHit) return sessionHit.id;
  }
  const firstFilm = entries.find((e) => e.filmVariant && stripEntryEnabled(e));
  if (firstFilm) return firstFilm.id;
  return entries.find((e) => stripEntryEnabled(e) && e.id !== "original")?.id ?? "";
}

function buildImageUrl(apiBase: string, pathQuoted: string, maxSide: number, rotateDeg = 0) {
  const path = filmPathQueryValue(pathQuoted);
  const rot = rotateDeg ? `&rotate=${rotateDeg}` : "";
  return `${apiBase}/image?path=${path}&max_side=${maxSide}${rot}`;
}

/** Normalize ``path`` query segment: single encode layer for FastAPI (works with urllib-quoted + raw paths). */
function filmPathQueryValue(pathFragment: string): string {
  const t = pathFragment.trim();
  if (!t) return t;
  try {
    return encodeURIComponent(decodeURIComponent(t));
  } catch {
    return encodeURIComponent(t);
  }
}

function buildFilmRenderUrl(
  apiBase: string,
  sourcePathQuoted: string,
  variant: string,
  maxSide: number,
  rotateDeg = 0,
  opticalPayload: Record<string, number> | null = null,
  adjustPayload: Record<string, number> | null = null,
) {
  const path = filmPathQueryValue(sourcePathQuoted);
  const rot = rotateDeg ? `&rotate=${rotateDeg}` : "";
  const opt =
    opticalPayload && Object.keys(opticalPayload).length > 0
      ? `&optical=${encodeURIComponent(JSON.stringify(opticalPayload))}`
      : "";
  const adj =
    adjustPayload && Object.keys(adjustPayload).length > 0
      ? `&adjust=${encodeURIComponent(JSON.stringify(adjustPayload))}`
      : "";
  return `${apiBase}/api/lab/film-render?path=${path}&variant=${encodeURIComponent(variant)}&max_side=${maxSide}${rot}${opt}${adj}`;
}

function resolveStripImageUrl(
  apiBase: string,
  entry: StyleStripEntry,
  maxSide: number,
  rotateDeg = 0,
): string | null {
  const pq = entry.pathQuoted?.trim();
  if (!pq) return null;
  if (entry.filmVariant) {
    return buildFilmRenderUrl(apiBase, pq, entry.filmVariant, maxSide, rotateDeg, null, entry.adjust ?? null);
  }
  return buildImageUrl(apiBase, pq, maxSide, rotateDeg);
}

function stripPlainImageUrl(
  apiBase: string,
  entry: StyleStripEntry,
  maxSide: number,
  rotateDeg = 0,
): string | null {
  const pq = entry.pathQuoted?.trim();
  if (!pq) return null;
  return buildImageUrl(apiBase, pq, maxSide, rotateDeg);
}

function stripFilmImageUrl(
  apiBase: string,
  entry: StyleStripEntry,
  maxSide: number,
  rotateDeg = 0,
  opticalPayload: Record<string, number> | null = null,
): string | null {
  const pq = entry.pathQuoted?.trim();
  if (!pq || !entry.filmVariant) return null;
  return buildFilmRenderUrl(apiBase, pq, entry.filmVariant, maxSide, rotateDeg, opticalPayload, entry.adjust ?? null);
}

/** 左栏 + 主图 + 右侧光学控制台 */
const LAB_BODY_GRID =
  "grid min-h-0 flex-1 grid-cols-[minmax(0,18%)_minmax(0,1fr)] lg:grid-cols-[minmax(0,18%)_minmax(0,1fr)_260px]";
const STAGE_IMAGE_MAX_H = "min(82dvh, calc(100dvh - 11rem))";
/** Fast plain JPEG for instant paint; film grade loads on top when ready. */
const MAIN_PREVIEW_PLAIN_MAX = 1100;
/** Lab main stage film-render — 896 hits server fast-JPEG path and keeps variant hops snappy. */
const MAIN_PREVIEW_FILM_MAX = 896;
/** Smaller side while optical is active — same as main film max (fast path). */
const MAIN_PREVIEW_FILM_OPTICAL_MAX = 896;
const OPTICAL_PREVIEW_DEBOUNCE_MS = 90;

function formatAiReason(item: GalleryItem): string | null {
  const rb = item.reason_bilingual;
  if (rb && typeof rb === "object") {
    const zh = rb.zh?.trim();
    const en = rb.en?.trim();
    return zh || en || null;
  }
  return item.reason?.trim() || null;
}

function scoreText(v: number | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return v.toFixed(1);
}

function aiOneLiner(item: GalleryItem): string | null {
  const raw = formatAiReason(item);
  if (!raw) return null;
  const one = raw.replace(/\s+/g, " ").trim();
  if (one.length <= 120) return one;
  return `${one.slice(0, 119)}…`;
}

type LabInfoPanelProps = {
  item: GalleryItem;
  sessionFilmVariant?: string | null;
  selectionKey: string;
  isSelected: boolean;
  likeReasons: CurationLikeReason[];
  onToggleSelection?: (checked: boolean) => void;
  onToggleLikeReason?: (reason: CurationLikeReason) => void;
};

function LabInfoPanel({
  item,
  sessionFilmVariant,
  selectionKey,
  isSelected,
  likeReasons,
  onToggleSelection,
  onToggleLikeReason,
}: LabInfoPanelProps) {
  const [metaOpen, setMetaOpen] = useState(false);
  const aiLine = aiOneLiner(item);
  const dims = item.width && item.height ? `${item.width} × ${item.height}` : null;
  const orient = item.orientation ? String(item.orientation) : null;
  const rot = Number(item.rotate_degrees ?? 0);
  const activeReasons = new Set(likeReasons);

  return (
    <aside
      className="flex h-full min-h-0 flex-col border-r border-white/[0.05] bg-[#080808]"
      aria-label="照片信息"
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2.5 py-3 sm:px-3 [scrollbar-width:thin] [scrollbar-color:rgba(255,255,255,0.1)_transparent]">
        <p
          className="break-all font-mono text-[10px] font-light leading-snug text-white/50"
          title={item.file ?? undefined}
        >
          {item.file ?? "—"}
        </p>

        <div className="mt-3">
          <p className="text-[22px] font-extralight tabular-nums leading-none tracking-tight text-white/88">
            {scoreText(item.overall_score)}
          </p>
          <p className="mt-1 text-[9px] font-light tabular-nums tracking-wide text-white/32">
            E {scoreText(item.energy)} · T {scoreText(item.technical)} · C {scoreText(item.composition)}
          </p>
        </div>

        {item.tags && item.tags.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-1">
            {item.tags.map((t) => (
              <li key={t} className="text-[9px] font-light text-white/35">
                #{t}
              </li>
            ))}
          </ul>
        ) : null}

        <p className="mt-3 text-[10px] font-light leading-relaxed text-white/42 line-clamp-4">
          {aiLine ?? <span className="text-white/20">暂无 AI 点评</span>}
        </p>

        {selectionKey && onToggleSelection ? (
          <div className="mt-4 space-y-2">
            <button
              type="button"
              aria-pressed={isSelected}
              onClick={() => onToggleSelection(!isSelected)}
              className={[
                "w-full rounded-[4px] py-2 text-[10px] font-normal tracking-[0.14em] transition-colors",
                isSelected
                  ? "bg-white/[0.12] text-white/90 ring-1 ring-white/20"
                  : "bg-white/[0.06] text-white/55 hover:bg-white/[0.09] hover:text-white/75",
              ].join(" ")}
            >
              {isSelected ? "Picked" : "Pick"}
            </button>
            {isSelected && onToggleLikeReason ? (
              <div className="flex flex-wrap gap-1" role="group" aria-label="喜欢的原因">
                {CURATION_LIKE_REASONS.map((id) => {
                  const on = activeReasons.has(id);
                  return (
                    <button
                      key={id}
                      type="button"
                      aria-pressed={on}
                      onClick={() => onToggleLikeReason(id)}
                      className={[
                        "rounded-[3px] px-1.5 py-0.5 text-[8px] transition-colors",
                        on ? "bg-white/10 text-white/65" : "text-white/28 hover:text-white/45",
                      ].join(" ")}
                    >
                      {CURATION_LIKE_REASON_LABELS[id]}
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>
        ) : null}

        <div className="mt-4 border-t border-white/[0.05] pt-2">
          <button
            type="button"
            aria-expanded={metaOpen}
            onClick={() => setMetaOpen((o) => !o)}
            className="flex w-full items-center gap-1.5 text-left text-[9px] font-light tracking-wide text-white/30 transition-colors hover:text-white/45"
          >
            <span className="inline-block w-3 text-center text-[10px] text-white/40" aria-hidden>
              {metaOpen ? "⌃" : "⌄"}
            </span>
            Metadata
          </button>
          {metaOpen ? (
            <dl className="mt-2 space-y-1 text-[9px] font-light text-white/38">
              <div className="flex justify-between gap-2">
                <dt className="text-white/22">尺寸</dt>
                <dd className="tabular-nums">{dims ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-white/22">方向</dt>
                <dd>{orient ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-white/22">旋转</dt>
                <dd className="tabular-nums">{rot ? `${rot}°` : "—"}</dd>
              </div>
              <div className="flex justify-between gap-2">
                <dt className="text-white/22">分类</dt>
                <dd>{item.category ?? "—"}</dd>
              </div>
              {sessionFilmVariant ? (
                <div className="flex justify-between gap-2">
                  <dt className="text-white/22">Vibe</dt>
                  <dd className="truncate text-right" title={sessionFilmVariant}>
                    {sessionFilmVariant}
                  </dd>
                </div>
              ) : null}
            </dl>
          ) : null}
        </div>
      </div>
    </aside>
  );
}

type LabToolbarProps = {
  previewLabel: string;
  onClose: () => void;
};

function LabToolbar({ previewLabel, onClose }: LabToolbarProps) {
  return (
    <header className="flex shrink-0 items-center justify-between gap-3 border-b border-white/[0.05] bg-[#050505] px-3 py-1.5 sm:px-4">
      <p className="min-w-0 truncate text-[10px] font-light tracking-wide text-white/28" title={previewLabel}>
        {previewLabel}
      </p>
      <div className="flex shrink-0 items-center gap-3">
        <span className="hidden text-[9px] font-light text-white/18 sm:inline">
          <kbd className="text-white/28">←→</kbd> <kbd className="text-white/28">JK</kbd>
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-[10px] font-light text-white/40 transition-colors hover:text-white/65"
          title="关闭 (Esc)"
        >
          Esc
        </button>
      </div>
    </header>
  );
}

type LabBottomBarProps = {
  canSetFinalResult: boolean;
  onClose: () => void;
  onSetFinalResult: () => void;
};

function LabBottomBar({ canSetFinalResult, onClose, onSetFinalResult }: LabBottomBarProps) {
  return (
    <footer className="flex shrink-0 items-center justify-end gap-2 border-t border-white/[0.05] bg-[#050505] px-3 py-1.5 sm:px-4">
      <button
        type="button"
        onClick={onClose}
        className="px-2 py-1 text-[10px] font-light text-white/35 transition-colors hover:text-white/55"
      >
        返回
      </button>
      <button
        type="button"
        disabled={!canSetFinalResult}
        onClick={onSetFinalResult}
        className="px-2 py-1 text-[10px] font-light text-white/45 transition-colors hover:text-white/65 disabled:text-white/18"
      >
        设为导出规格
      </button>
    </footer>
  );
}

type MainPreviewProps = {
  cacheKey: string;
  plainUrl: string | null;
  /** When set, load after plain is shown (progressive). */
  filmUrl: string | null;
};

/** Variant + rotate segment of ``previewCacheKey`` (optical suffix excluded). */
function filmPreviewVariantKey(cacheKey: string): string {
  const parts = cacheKey.split(":");
  if (parts.length < 3) return cacheKey;
  return parts.slice(0, 3).join(":");
}

/** 主图：先原图立即可见，胶片 LUT 后台加载后叠上（与 strip 缩略图策略一致）。 */
function MainPreview({ cacheKey, plainUrl, filmUrl }: MainPreviewProps) {
  const instantUrl = plainUrl ?? filmUrl;
  const [displaySrc, setDisplaySrc] = useState<string | null>(instantUrl);
  const [gradeReady, setGradeReady] = useState(() => !filmUrl || filmUrl === plainUrl);
  const loadGenRef = useRef(0);
  const lastVariantKeyRef = useRef("");
  const displaySrcRef = useRef(displaySrc);
  const gradeReadyRef = useRef(gradeReady);
  displaySrcRef.current = displaySrc;
  gradeReadyRef.current = gradeReady;

  useEffect(() => {
    const gen = ++loadGenRef.current;
    const noGrade = !filmUrl || filmUrl === plainUrl;
    const variantKey = filmPreviewVariantKey(cacheKey);
    const opticalOnlyReload =
      Boolean(lastVariantKeyRef.current) &&
      lastVariantKeyRef.current === variantKey &&
      variantKey !== "none";
    lastVariantKeyRef.current = variantKey;

    if (noGrade || !filmUrl) {
      setGradeReady(true);
      setDisplaySrc(plainUrl ?? filmUrl);
      return;
    }

    const nextIsFilmGrade = Boolean(plainUrl && filmUrl !== plainUrl);
    const holdPreviousGrade =
      nextIsFilmGrade &&
      gradeReadyRef.current &&
      Boolean(displaySrcRef.current) &&
      displaySrcRef.current !== plainUrl &&
      displaySrcRef.current !== filmUrl;

    if (!opticalOnlyReload && !holdPreviousGrade) {
      setGradeReady(false);
      setDisplaySrc(plainUrl ?? filmUrl);
    }

    if (isFilmPreviewWarmed(filmUrl)) {
      setDisplaySrc(filmUrl);
      setGradeReady(true);
      return;
    }

    const probe = new Image();
    probe.onload = () => {
      if (loadGenRef.current !== gen) return;
      setDisplaySrc(filmUrl);
      setGradeReady(true);
    };
    probe.onerror = () => {
      if (loadGenRef.current !== gen) return;
      setGradeReady(true);
    };
    probe.src = filmUrl;
    preloadFilmPreviewUrl(filmUrl);

    return () => {
      loadGenRef.current = gen + 1;
    };
  }, [cacheKey, plainUrl, filmUrl]);

  const onImgError = useCallback(() => {
    if (plainUrl && displaySrc !== plainUrl) setDisplaySrc(plainUrl);
    setGradeReady(true);
  }, [plainUrl, displaySrc]);

  const pendingGrade = Boolean(filmUrl && filmUrl !== plainUrl && !gradeReady);

  return (
    <section className="relative flex min-h-0 flex-1 flex-col bg-black" aria-label="主图预览">
      <div className="flex min-h-0 flex-1 items-center justify-center px-3 py-2 sm:px-4">
        {displaySrc ? (
          <div className="relative flex max-h-full max-w-full items-center justify-center">
            <div
              className="pointer-events-none absolute inset-[-12%] rounded-lg opacity-40 blur-3xl"
              style={{
                background:
                  "radial-gradient(ellipse 70% 60% at 50% 50%, rgba(255,255,255,0.07) 0%, transparent 72%)",
              }}
              aria-hidden
            />
            <img
              src={displaySrc}
              alt=""
              className={[
                "relative max-w-full object-contain transition-[opacity,filter] duration-75 ease-out",
                pendingGrade ? "opacity-[0.92] saturate-[0.97]" : "opacity-100 saturate-100",
              ].join(" ")}
              style={{
                maxHeight: STAGE_IMAGE_MAX_H,
                width: "auto",
                height: "auto",
                filter:
                  "drop-shadow(0 0 42px rgba(255,255,255,0.06)) drop-shadow(0 24px 64px rgba(0,0,0,0.75))",
              }}
              draggable={false}
              onError={onImgError}
            />
          </div>
        ) : (
          <div className="text-[11px] font-light text-white/20">暂无预览</div>
        )}
      </div>
    </section>
  );
}

/** Pick the strip thumb whose center is closest to the scroll container midpoint. */
function pickCenteredLutId(container: HTMLElement): string | null {
  const rect = container.getBoundingClientRect();
  const centerX = rect.left + rect.width / 2;
  const nodes = container.querySelectorAll<HTMLElement>("[data-lut-id]");
  let best: string | null = null;
  let bestDist = Infinity;
  nodes.forEach((node) => {
    const id = node.dataset.lutId?.trim();
    if (!id) return;
    const r = node.getBoundingClientRect();
    const dist = Math.abs(r.left + r.width / 2 - centerX);
    if (dist < bestDist) {
      bestDist = dist;
      best = id;
    }
  });
  return best;
}

type StyleSelectorProps = {
  entries: StyleStripEntry[];
  selectedId: string;
  apiBase: string;
  rotateDeg: number;
  onSelect: (id: string) => void;
  chosenExport?: GalleryExportItem | null;
  /** Skip scroll→selection sync while programmatic scroll (click / keyboard) runs. */
  suppressScrollSyncUntil: MutableRefObject<number>;
};

type LutStripThumbProps = {
  entry: StyleStripEntry;
  apiBase: string;
  rotateDeg: number;
  selected: boolean;
  exportPicked: boolean;
  onSelect: () => void;
  onWarmFilmPreview?: () => void;
};

/** 先原图立即可见，进视口再换 film-render；失败回退原图。 */
function LutStripThumb({
  entry,
  apiBase,
  rotateDeg,
  selected,
  exportPicked,
  onSelect,
  onWarmFilmPreview,
}: LutStripThumbProps) {
  const thumbMax = 360;
  const plainUrl = stripPlainImageUrl(apiBase, entry, thumbMax, rotateDeg);
  const filmUrl = stripFilmImageUrl(apiBase, entry, thumbMax, rotateDeg);
  const disabled = !plainUrl && !filmUrl;
  const initial = plainUrl ?? filmUrl ?? "";

  const rootRef = useRef<HTMLButtonElement>(null);
  const upgradedRef = useRef(false);
  const [src, setSrc] = useState(initial);

  useEffect(() => {
    upgradedRef.current = false;
    setSrc(plainUrl ?? filmUrl ?? "");
  }, [plainUrl, filmUrl, entry.id]);

  useEffect(() => {
    if (!filmUrl || !plainUrl || filmUrl === plainUrl) return;
    const root = rootRef.current;
    if (!root) return;

    const observer = new IntersectionObserver(
      (hits) => {
        if (!hits.some((e) => e.isIntersecting) || upgradedRef.current) return;
        upgradedRef.current = true;
        observer.disconnect();
        const probe = new Image();
        probe.onload = () => setSrc(filmUrl);
        probe.onerror = () => {
          /* 保持原图 */
        };
        probe.src = filmUrl;
      },
      { root: null, rootMargin: "0px 180px", threshold: 0.01 },
    );
    observer.observe(root);
    return () => observer.disconnect();
  }, [filmUrl, plainUrl, entry.id]);

  const onImgError = useCallback(() => {
    if (plainUrl && src !== plainUrl) setSrc(plainUrl);
  }, [plainUrl, src]);

  useEffect(() => {
    if (!selected || !rootRef.current) return;
    rootRef.current.scrollIntoView({ behavior: "auto", inline: "center", block: "nearest" });
  }, [selected, entry.id]);

  return (
    <button
      ref={rootRef}
      type="button"
      data-lut-id={entry.id}
      disabled={disabled}
      title={entry.label}
      onClick={() => !disabled && onSelect()}
      onMouseEnter={() => onWarmFilmPreview?.()}
      onFocus={() => onWarmFilmPreview?.()}
      className={[
        "group/lut relative h-[4.5rem] w-12 shrink-0 overflow-hidden rounded-[3px] outline-none transition-[box-shadow,filter] duration-200 sm:h-20 sm:w-[3.25rem]",
        disabled ? "cursor-not-allowed" : "",
        selected
          ? "ring-1 ring-inset ring-white/40 shadow-[0_0_14px_rgba(255,255,255,0.08)]"
          : "ring-1 ring-inset ring-white/[0.06] hover:ring-white/20",
        exportPicked && !selected ? "ring-emerald-500/30" : "",
      ].join(" ")}
    >
      <div className="relative h-full w-full bg-[#0a0a0a]">
        {src ? (
          <img
            src={src}
            alt=""
            className={[
              "h-full w-full object-cover transition-[filter,opacity] duration-200",
              selected
                ? "opacity-100 brightness-100 saturate-100"
                : "opacity-[0.72] brightness-[0.82] saturate-[0.9] group-hover/lut:opacity-95 group-hover/lut:brightness-95",
            ].join(" ")}
            loading="lazy"
            decoding="async"
            onError={onImgError}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[8px] text-white/20">—</div>
        )}
      </div>
    </button>
  );
}

function StyleSelector({
  entries,
  selectedId,
  apiBase,
  rotateDeg,
  onSelect,
  chosenExport,
  suppressScrollSyncUntil,
}: StyleSelectorProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const syncSelectionToScroll = useCallback(() => {
    if (Date.now() < suppressScrollSyncUntil.current) return;
    const el = scrollRef.current;
    if (!el) return;
    const id = pickCenteredLutId(el);
    if (id && id !== selectedId) onSelect(id);
  }, [onSelect, selectedId, suppressScrollSyncUntil]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    let raf = 0;
    const onScroll = () => {
      if (raf) return;
      raf = window.requestAnimationFrame(() => {
        raf = 0;
        syncSelectionToScroll();
      });
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    el.addEventListener("scrollend", syncSelectionToScroll);
    return () => {
      if (raf) window.cancelAnimationFrame(raf);
      el.removeEventListener("scroll", onScroll);
      el.removeEventListener("scrollend", syncSelectionToScroll);
    };
  }, [syncSelectionToScroll]);

  return (
    <div
      className="shrink-0 border-t border-white/[0.05] bg-[#030303]"
      data-lut-rail
      aria-label="Film strip"
    >
      <div ref={scrollRef} className="lab-lut-scroll px-3 py-3 sm:px-4">
        {entries.map((entry) => {
          const enabled = stripEntryEnabled(entry);
          const selected = entry.id === selectedId && enabled;
          const exportPicked = entryMatchesChosenExport(entry, chosenExport);
          return (
            <LutStripThumb
              key={entry.id}
              entry={entry}
              apiBase={apiBase}
              rotateDeg={rotateDeg}
              selected={selected}
              exportPicked={exportPicked}
              onSelect={() => onSelect(entry.id)}
              onWarmFilmPreview={() => {
                const u = stripFilmImageUrl(apiBase, entry, MAIN_PREVIEW_FILM_MAX, rotateDeg);
                preloadFilmPreviewUrl(u);
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

export function LabCompareModal({
  item,
  apiBase,
  onClose,
  chosenExport,
  onPickExportStyle,
  sessionFilmVariant,
  selectionKey = "",
  isSelected = false,
  likeReasons = [],
  onToggleSelection,
  onToggleLikeReason,
}: Props) {
  const rotateDeg = Number(item?.rotate_degrees ?? 0);
  const entries = useMemo(() => (item ? buildStyleEntries(item) : []), [item]);
  const [selectedId, setSelectedId] = useState("");
  const suppressScrollSyncUntil = useRef(0);
  const [optical, setOptical] = useState<OpticalConsoleState>(OPTICAL_NEUTRAL);
  const [debouncedOptical, setDebouncedOptical] = useState<OpticalConsoleState>(OPTICAL_NEUTRAL);
  const opticalDebounceRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    setOptical(OPTICAL_NEUTRAL);
    setDebouncedOptical(OPTICAL_NEUTRAL);
  }, [item?.path, item?.file]);

  useEffect(() => {
    if (opticalDebounceRef.current) window.clearTimeout(opticalDebounceRef.current);
    opticalDebounceRef.current = window.setTimeout(
      () => setDebouncedOptical(optical),
      OPTICAL_PREVIEW_DEBOUNCE_MS,
    );
    return () => {
      if (opticalDebounceRef.current) window.clearTimeout(opticalDebounceRef.current);
    };
  }, [optical]);

  const flushOpticalPreview = useCallback(() => {
    if (opticalDebounceRef.current) window.clearTimeout(opticalDebounceRef.current);
    setDebouncedOptical(optical);
  }, [optical]);

  const opticalApiPayload = useMemo(
    () => opticalStateToApiPayload(debouncedOptical),
    [debouncedOptical],
  );
  const opticalPending = opticalCacheKey(optical) !== opticalCacheKey(debouncedOptical);

  const onStripSelect = useCallback((id: string) => {
    suppressScrollSyncUntil.current = Date.now() + 180;
    setSelectedId(id);
  }, []);

  useEffect(() => {
    if (!item) {
      setSelectedId("");
      return;
    }
    const next = buildStyleEntries(item);
    setSelectedId((prev) => {
      if (prev && next.some((e) => e.id === prev && stripEntryEnabled(e))) return prev;
      return pickDefaultSelectedId(next, sessionFilmVariant);
    });
  }, [item, sessionFilmVariant]);

  const isOpen = Boolean(item && entries.some(stripEntryEnabled));

  useEffect(() => {
    if (!isOpen) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, [isOpen]);

  const selected = useMemo(() => {
    const hit = entries.find((e) => e.id === selectedId && stripEntryEnabled(e));
    if (hit) return hit;
    const defId = pickDefaultSelectedId(entries, sessionFilmVariant);
    const def = entries.find((e) => e.id === defId && stripEntryEnabled(e));
    return def ?? entries.find((e) => stripEntryEnabled(e)) ?? null;
  }, [entries, selectedId]);

  const previewPlainUrl = useMemo(() => {
    if (!selected) return null;
    return stripPlainImageUrl(apiBase, selected, MAIN_PREVIEW_PLAIN_MAX, rotateDeg);
  }, [selected, apiBase, rotateDeg]);

  const opticalPreviewActive = useMemo(
    () => Boolean(opticalStateToApiPayload(optical) || opticalApiPayload),
    [optical, opticalApiPayload],
  );

  const previewFilmUrl = useMemo(() => {
    if (!selected) return null;
    const filmMax = opticalPreviewActive ? MAIN_PREVIEW_FILM_OPTICAL_MAX : MAIN_PREVIEW_FILM_MAX;
    if (selected.filmVariant) {
      return stripFilmImageUrl(
        apiBase,
        selected,
        filmMax,
        rotateDeg,
        opticalApiPayload,
      );
    }
    return resolveStripImageUrl(apiBase, selected, MAIN_PREVIEW_PLAIN_MAX, rotateDeg);
  }, [selected, apiBase, rotateDeg, opticalApiPayload, opticalPreviewActive]);

  const previewCacheKey = selected
    ? `${selected.id}:${selected.filmVariant ?? "plain"}:${rotateDeg}:${opticalCacheKey(debouncedOptical)}`
    : "none";

  const enabledEntryIds = useMemo(
    () => entries.filter(stripEntryEnabled).map((e) => e.id),
    [entries],
  );

  useEffect(() => {
    if (!isOpen || !item || enabledEntryIds.length === 0) return;
    const idx = Math.max(0, enabledEntryIds.indexOf(selectedId));
    const n = enabledEntryIds.length;
    const neighborIds: string[] = [];
    for (let d = -6; d <= 6; d += 1) {
      if (d === 0) continue;
      neighborIds.push(enabledEntryIds[(idx + d + n) % n]!);
    }
    const urls = neighborIds
      .map((id) => entries.find((e) => e.id === id))
      .filter((e): e is StyleStripEntry => Boolean(e?.filmVariant))
      .map((e) =>
        stripFilmImageUrl(apiBase, e, MAIN_PREVIEW_FILM_MAX, rotateDeg, opticalApiPayload),
      );
    preloadFilmPreviewUrls(urls);
  }, [isOpen, item, selectedId, enabledEntryIds, entries, apiBase, rotateDeg, opticalApiPayload]);

  useEffect(() => {
    if (!isOpen || !item) return;
    const urls = entries
      .filter((e) => e.filmVariant && stripEntryEnabled(e))
      .map((e) => stripFilmImageUrl(apiBase, e, MAIN_PREVIEW_FILM_MAX, rotateDeg));
    preloadFilmPreviewUrls(urls);
  }, [isOpen, item?.path, item?.file, entries, apiBase, rotateDeg]);

  useEffect(() => {
    if (!isOpen || !previewFilmUrl) return;
    preloadFilmPreviewUrl(previewFilmUrl);
  }, [isOpen, previewFilmUrl]);

  const previewLabel = selected?.label ?? "—";

  const stepStripSelection = useCallback(
    (delta: number) => {
      if (enabledEntryIds.length === 0) return;
      const cur = enabledEntryIds.indexOf(selectedId);
      const base = cur >= 0 ? cur : 0;
      const next = (base + delta + enabledEntryIds.length) % enabledEntryIds.length;
      suppressScrollSyncUntil.current = Date.now() + 180;
      setSelectedId(enabledEntryIds[next]!);
    },
    [enabledEntryIds, selectedId],
  );

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (
        e.key === "ArrowRight" ||
        e.key === "ArrowDown" ||
        e.key === "j" ||
        e.key === "J"
      ) {
        e.preventDefault();
        stepStripSelection(1);
      } else if (
        e.key === "ArrowLeft" ||
        e.key === "ArrowUp" ||
        e.key === "k" ||
        e.key === "K"
      ) {
        e.preventDefault();
        stepStripSelection(-1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, isOpen, stepStripSelection]);

  const finalExportSpec = useMemo(
    () => (item && selected ? styleEntryToGalleryExportItem(item, selected) : null),
    [item, selected],
  );
  const canSetFinalResult = Boolean(onPickExportStyle && finalExportSpec);

  const onSetFinalResult = useCallback(() => {
    if (!onPickExportStyle || !item || !selected || !finalExportSpec) return;
    onPickExportStyle(finalExportSpec, selected.label);
  }, [onPickExportStyle, item, selected, finalExportSpec]);

  if (!item || !entries.some(stripEntryEnabled)) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col font-[system-ui,-apple-system,sans-serif]"
      role="dialog"
      aria-modal="true"
      aria-label="风格对比"
    >
      <button
        type="button"
        className="absolute inset-0 z-0 border-0 bg-[#0a0a0a]/[0.96] backdrop-blur-[32px] backdrop-brightness-[0.45] backdrop-saturate-150 motion-reduce:backdrop-blur-sm motion-reduce:backdrop-brightness-100"
        aria-label="关闭预览，返回相册"
        onClick={onClose}
      />

      <div
        className="relative z-10 flex h-[100dvh] min-h-0 w-full flex-col pointer-events-auto overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <LabToolbar previewLabel={previewLabel} onClose={onClose} />

        <div className={LAB_BODY_GRID}>
          <LabInfoPanel
            item={item}
            sessionFilmVariant={sessionFilmVariant}
            selectionKey={selectionKey}
            isSelected={Boolean(selectionKey && isSelected)}
            likeReasons={likeReasons}
            onToggleSelection={selectionKey ? onToggleSelection : undefined}
            onToggleLikeReason={selectionKey ? onToggleLikeReason : undefined}
          />
          <div className="flex min-h-0 min-w-0 flex-col">
            <MainPreview
              cacheKey={previewCacheKey}
              plainUrl={previewPlainUrl}
              filmUrl={previewFilmUrl}
            />
            <StyleSelector
              entries={entries}
              selectedId={selectedId}
              apiBase={apiBase}
              rotateDeg={rotateDeg}
              onSelect={onStripSelect}
              chosenExport={chosenExport}
              suppressScrollSyncUntil={suppressScrollSyncUntil}
            />
          </div>
          <div className="hidden min-h-0 lg:flex">
            <OpticalConsolePanel
              value={optical}
              onChange={setOptical}
              onScrubEnd={flushOpticalPreview}
              filmMode={Boolean(selected?.filmVariant)}
              pendingPreview={opticalPending && Boolean(selected?.filmVariant)}
            />
          </div>
        </div>

        <LabBottomBar
          canSetFinalResult={canSetFinalResult}
          onClose={onClose}
          onSetFinalResult={onSetFinalResult}
        />
      </div>
    </div>
  );
}
