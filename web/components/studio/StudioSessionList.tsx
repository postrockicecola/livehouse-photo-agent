"use client";

import type { StudioSessionRow } from "@/lib/studioApi";
import { shortenPath } from "@/lib/studioApi";
import { buildStudioCoverUrl, sessionDateFromKey } from "@/lib/studioUi";
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
  return (
    <footer id="sessions" className="w-full scroll-mt-16">
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
        className="columns-2 gap-1.5 sm:columns-3 [column-fill:_balance]"
        aria-label="Session projects"
      >
        {setList.map((row) => {
          const sel = selectedPreviewsDir === row.previews_dir;
          const act = activePreviewsDir === row.previews_dir;
          const coverUrl = buildStudioCoverUrl(row.cover_path_quoted, 400);
          const date = sessionDateFromKey(row.session_key);
          const displayStatus = sessionDisplayStatus(row, act);

          return (
            <li key={row.previews_dir || row.session_key} className="mb-1.5 break-inside-avoid">
              <button
                type="button"
                onClick={() => onSelect(row)}
                className={`group relative block w-full cursor-pointer overflow-hidden rounded-md text-left transition-opacity ${
                  sel ? "ring-1 ring-white/25 ring-offset-1 ring-offset-[#0e0e0e]" : "hover:opacity-90"
                }`}
              >
                <div className="relative overflow-hidden bg-[#161616]">
                  {coverUrl ? (
                    <img
                      src={coverUrl}
                      alt=""
                      className="block h-auto w-full object-cover transition-transform duration-200 group-hover:scale-[1.03]"
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <div className="aspect-[3/2] w-full bg-[linear-gradient(135deg,#1a1000,#4a2a00)]" />
                  )}
                  <div className="absolute inset-x-0 bottom-0 bg-[linear-gradient(transparent,rgba(0,0,0,0.7))] px-2 pb-1.5 pt-[18px]">
                    {displayStatus === "active" ? (
                      <p className="text-[9px] text-[rgba(64,200,200,0.8)]">● Live</p>
                    ) : displayStatus === "completed" ? (
                      <p className="text-[10px] text-white/70">
                        {row.preview_count.toLocaleString("en-US")} photos
                      </p>
                    ) : displayStatus === "failed" ? (
                      <p className="text-[10px] text-rose-300/80">Failed</p>
                    ) : (
                      <p className="text-[10px] text-white/70">
                        {row.preview_count.toLocaleString("en-US")} photos
                      </p>
                    )}
                    <p className="text-[9px] text-white/40">{date}</p>
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
