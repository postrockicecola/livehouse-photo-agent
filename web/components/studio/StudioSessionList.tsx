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
          className="shrink-0 text-[10px] uppercase tracking-[0.08em] text-white/20 transition-colors hover:text-white/45"
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
                title={[date, band].filter(Boolean).join(" · ")}
                className={`group relative block h-full w-full cursor-pointer overflow-hidden rounded-md text-left ${
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
                        alt={band || date}
                        className="block h-full w-full object-cover transition-[transform,filter] duration-300 group-hover:scale-[1.04] group-hover:blur-[6px] group-hover:brightness-[0.45]"
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
                    <div className="h-full w-full bg-[linear-gradient(135deg,#1a1000,#4a2a00)] transition-[filter,brightness] duration-300 group-hover:blur-[6px] group-hover:brightness-[0.45]" />
                  )}

                  {/* Hover readout: date → band → funnel */}
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-3.5 text-center opacity-0 transition-opacity duration-300 group-hover:opacity-100">
                    <p className="text-[13px] tabular-nums tracking-[0.04em] text-white/75 sm:text-[14px]">
                      {date}
                    </p>
                    {band ? (
                      <p className="mt-2 max-w-full truncate text-[17px] font-medium leading-tight text-white sm:text-[19px]">
                        {band}
                      </p>
                    ) : (
                      <p className="mt-2 text-[15px] text-white/50 sm:text-[16px]">{row.session_key}</p>
                    )}
                    {funnelLine ? (
                      <p className="mt-2.5 max-w-full text-[12px] leading-snug text-white/60 [overflow-wrap:anywhere] sm:text-[13px]">
                        {funnelLine}
                      </p>
                    ) : null}
                    {displayStatus === "active" ? (
                      <p className="mt-2 text-[9px] text-[rgba(64,200,200,0.9)]">● Live</p>
                    ) : displayStatus === "failed" ? (
                      <p className="mt-2 text-[10px] text-rose-300/85">Failed</p>
                    ) : null}
                  </div>

                  {displayStatus === "active" ? (
                    <span className="absolute left-2 top-2 rounded-full bg-black/45 px-1.5 py-0.5 text-[9px] text-[rgba(64,200,200,0.9)] transition-opacity duration-300 group-hover:opacity-0">
                      ● Live
                    </span>
                  ) : null}
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
