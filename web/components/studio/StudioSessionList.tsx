"use client";

import { useCallback, useEffect, useRef } from "react";

import type { StudioSessionRow } from "@/lib/studioApi";
import { shortenPath } from "@/lib/studioApi";
import {
  buildStudioCoverUrl,
  formatSessionFunnelLine,
  sessionDisplayDate,
} from "@/lib/studioUi";
import type { StudioSessionSortOrder } from "@/lib/studioSessionSort";

const FAILED_JOB_STATUSES = new Set([
  "FAILED_PERMANENT",
  "FAILED_RETRYABLE",
  "DEAD_LETTERED",
  "CANCELLED",
]);

export type SessionDisplayStatus = "active" | "completed" | "failed";

export function sessionDisplayStatus(row: StudioSessionRow, isActive: boolean): SessionDisplayStatus | null {
  if (isActive) return "active";
  const job = String(row.last_job_status ?? "").trim();
  if (job && FAILED_JOB_STATUSES.has(job)) return "failed";
  if (row.has_analysis_results) return "completed";
  return null;
}

// Masonry base unit + gutter (px). Each tile spans however many ROW_UNIT rows
// its cover's aspect ratio needs, so portraits stay tall and landscapes wide —
// a packed, aspect-preserving mosaic instead of uniform crops.
const ROW_UNIT = 8;
const GAP = 16;
const FALLBACK_RATIO = 0.667; // height / width for cover-less tiles (~3:2)

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0e0e0e]";

type Props = {
  setList: StudioSessionRow[];
  setListSort: StudioSessionSortOrder;
  selectedPreviewsDir: string | undefined;
  activePreviewsDir: string | undefined;
  loading: boolean;
  archiveRoot: string;
  onSelect: (row: StudioSessionRow) => void;
  onToggleSort: () => void;
};

export function StudioSessionList({
  setList,
  setListSort,
  selectedPreviewsDir,
  activePreviewsDir,
  loading,
  archiveRoot,
  onSelect,
  onToggleSort,
}: Props) {
  const gridRef = useRef<HTMLUListElement>(null);
  const itemRefs = useRef(new Map<string, HTMLLIElement>());
  const ratios = useRef(new Map<string, number>());

  const applySpan = useCallback((li: HTMLLIElement, ratio: number) => {
    const width = li.clientWidth;
    if (!width) return;
    const height = width * ratio;
    const span = Math.max(1, Math.ceil((height + GAP) / (ROW_UNIT + GAP)));
    li.style.gridRowEnd = `span ${span}`;
  }, []);

  const recomputeAll = useCallback(() => {
    itemRefs.current.forEach((li, key) => {
      applySpan(li, ratios.current.get(key) ?? FALLBACK_RATIO);
    });
  }, [applySpan]);

  useEffect(() => {
    recomputeAll();
    const el = gridRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => recomputeAll());
    ro.observe(el);
    return () => ro.disconnect();
  }, [recomputeAll, setList]);

  // Cancel main `px-6` equally on both sides, then apply matching gutters.
  return (
    <footer id="sessions" className="-mx-6 scroll-mt-16 px-4 sm:px-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <p className="text-[10px] uppercase tracking-[0.1em] text-white/30">Sessions — recent</p>
        <button
          type="button"
          onClick={onToggleSort}
          className={`shrink-0 text-[10px] uppercase tracking-[0.08em] text-white/20 transition-colors hover:text-white/45 ${focusRing}`}
          title="Sort by session date"
        >
          {setListSort === "desc" ? "New → Old" : "Old → New"}
        </button>
      </div>

      <ul
        ref={gridRef}
        className="grid grid-cols-2 gap-4 sm:grid-cols-3"
        style={{ gridAutoRows: `${ROW_UNIT}px` }}
        aria-label="Session projects"
      >
        {setList.map((row) => {
          const key = row.previews_dir || row.session_key;
          const sel = selectedPreviewsDir === row.previews_dir;
          const act = activePreviewsDir === row.previews_dir;
          const coverUrl = buildStudioCoverUrl(row.cover_path_quoted, 600);
          const coverPortraitUrl = buildStudioCoverUrl(row.cover_portrait_path_quoted, 600);
          const date = sessionDisplayDate(row);
          const band = row.band_name?.trim() || "";
          const funnelLine = formatSessionFunnelLine(row);
          const displayStatus = sessionDisplayStatus(row, act);
          const titleLine = band || row.session_key;

          return (
            <li
              key={key}
              ref={(el) => {
                if (el) {
                  itemRefs.current.set(key, el);
                  applySpan(el, ratios.current.get(key) ?? FALLBACK_RATIO);
                } else {
                  itemRefs.current.delete(key);
                }
              }}
            >
              <button
                type="button"
                onClick={() => onSelect(row)}
                title={[date, titleLine, funnelLine].filter(Boolean).join(" · ")}
                aria-label={[date, titleLine, funnelLine, displayStatus].filter(Boolean).join(", ")}
                className={`group relative block h-full w-full cursor-pointer overflow-hidden rounded-md text-left ${focusRing} ${
                  sel ? "ring-1 ring-white/25 ring-offset-1 ring-offset-[#0e0e0e]" : ""
                }`}
              >
                <div className="relative h-full w-full overflow-hidden bg-[#161616]">
                  {coverUrl ? (
                    <picture>
                      {coverPortraitUrl ? (
                        <source media="(max-width: 639px)" srcSet={coverPortraitUrl} />
                      ) : null}
                      <img
                        src={coverUrl}
                        alt=""
                        className="block h-full w-full object-cover transition-transform duration-300 [@media(hover:hover)]:group-hover:scale-[1.03]"
                        loading="lazy"
                        decoding="async"
                        onLoad={(e) => {
                          const img = e.currentTarget;
                          if (img.naturalWidth > 0) {
                            const ratio = img.naturalHeight / img.naturalWidth;
                            ratios.current.set(key, ratio);
                            const li = itemRefs.current.get(key);
                            if (li) applySpan(li, ratio);
                          }
                        }}
                      />
                    </picture>
                  ) : (
                    <div className="h-full w-full bg-[#1a140c]" />
                  )}

                  {/* Always-on caption — readable on touch without hover */}
                  <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/80 via-black/45 to-transparent px-2.5 pb-2.5 pt-10">
                    <div className="flex items-end justify-between gap-2">
                      <div className="min-w-0">
                        <p className="truncate text-[11px] font-medium leading-tight text-white/92 sm:text-[12px]">
                          {titleLine}
                        </p>
                        <p className="mt-0.5 truncate text-[10px] tabular-nums text-white/55">{date}</p>
                        {funnelLine ? (
                          <p className="mt-0.5 truncate text-[9px] text-white/40 sm:text-[10px]">{funnelLine}</p>
                        ) : null}
                      </div>
                      {displayStatus === "active" ? (
                        <span className="shrink-0 rounded-full bg-black/40 px-1.5 py-0.5 text-[9px] text-[rgba(64,200,200,0.95)]">
                          Live
                        </span>
                      ) : displayStatus === "failed" ? (
                        <span className="shrink-0 rounded-full bg-black/40 px-1.5 py-0.5 text-[9px] text-rose-300/90">
                          Failed
                        </span>
                      ) : displayStatus === "completed" ? (
                        <span className="shrink-0 rounded-full bg-black/40 px-1.5 py-0.5 text-[9px] text-white/45">
                          Done
                        </span>
                      ) : null}
                    </div>
                  </div>
                </div>
              </button>
            </li>
          );
        })}
      </ul>

      {!loading ? (
        <p className="mt-3 text-[10px] tabular-nums text-white/25">{setList.length} sessions</p>
      ) : null}

      {archiveRoot ? (
        <p className="mt-1 truncate text-[10px] text-white/20" title={archiveRoot}>
          {shortenPath(archiveRoot, 64)}
        </p>
      ) : null}
    </footer>
  );
}
