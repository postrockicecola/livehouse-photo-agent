"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import type { StudioSessionRow, StudioStatusResponse } from "@/lib/studioApi";
import { buildStudioCoverUrl, formatElapsed, jobStatusLabel, sessionDisplayDate } from "@/lib/studioUi";

type Props = {
  session: StudioSessionRow;
  status: StudioStatusResponse | null;
  isActive: boolean;
  jobRunning: boolean;
  canGallery: boolean;
  analyzeLocked: boolean;
  busy: "activate" | "analyze" | null;
  onActivate: () => void;
  onAnalyze: () => void;
  /** Rendered over the same hero background image (e.g. workflow timeline + stats). */
  children?: ReactNode;
};

function StatusBadge({ label, running }: { label: string; running: boolean }) {
  const succeeded = label === "SUCCEEDED";
  const failed = label.includes("FAILED") || label === "DEAD_LETTERED" || label === "CANCELLED";

  if (running) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded border border-amber-400/30 bg-amber-400/10 px-2.5 py-0.5 text-[11px] tracking-[0.06em] text-amber-200/90">
        <span className="h-[5px] w-[5px] animate-pulse rounded-full bg-amber-300" />
        Running
      </span>
    );
  }

  if (succeeded) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded border border-[rgba(29,158,117,0.3)] bg-[rgba(29,158,117,0.15)] px-2.5 py-0.5 text-[11px] tracking-[0.06em] text-[#5dcaa5]">
        <span className="h-[5px] w-[5px] rounded-full bg-[#5dcaa5]" />
        Succeeded
      </span>
    );
  }

  if (failed) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded border border-rose-400/30 bg-rose-500/10 px-2.5 py-0.5 text-[11px] tracking-[0.06em] text-rose-300/90">
        <span className="h-[5px] w-[5px] rounded-full bg-rose-400" />
        {label.replace(/_/g, " ")}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-white/15 bg-white/[0.06] px-2.5 py-0.5 text-[11px] tracking-[0.06em] text-white/70">
      {label}
    </span>
  );
}

function formatElapsedSpaced(sec: number | null | undefined): string {
  const raw = formatElapsed(sec);
  if (raw === "—") return raw;
  return raw.replace(/(\d+)m(\d)/, "$1m $2");
}

export function StudioCurrentSessionHero({
  session,
  status,
  isActive,
  jobRunning,
  canGallery,
  analyzeLocked,
  busy,
  onActivate,
  onAnalyze,
  children,
}: Props) {
  const dateLabel = sessionDisplayDate(session);
  const bandName = session.band_name?.trim() || "";
  const photoCount = status?.session?.preview_count ?? session.preview_count;
  const elapsed = status?.job?.elapsed_sec ?? null;
  const jobStatus = jobStatusLabel(status?.job?.status, jobRunning);
  const heroUrl = buildStudioCoverUrl(session.cover_path_quoted, 1280);
  const heroPortraitUrl = buildStudioCoverUrl(session.cover_portrait_path_quoted, 1280);

  return (
    <section aria-labelledby="studio-current-session">
      <div className="relative flex min-h-[calc(100svh-42px)] flex-col overflow-hidden border-b border-white/[0.07]">
        {heroUrl ? (
          <picture>
            {heroPortraitUrl ? (
              <source media="(max-width: 767px)" srcSet={heroPortraitUrl} />
            ) : null}
            <img
              src={heroUrl}
              alt={bandName || dateLabel}
              className="absolute inset-0 h-full w-full object-cover object-[50%_35%] opacity-65"
              decoding="async"
              fetchPriority="high"
            />
          </picture>
        ) : (
          <>
            <div
              className="absolute inset-0 bg-[linear-gradient(135deg,#1a1200_0%,#3d2800_40%,#1a0a00_100%)]"
              aria-hidden
            />
            <div
              className="absolute inset-0 bg-[radial-gradient(ellipse_at_30%_60%,rgba(255,180,30,0.25)_0%,transparent_60%)]"
              aria-hidden
            />
          </>
        )}
        <div
          className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(14,14,14,0.82)_0%,rgba(14,14,14,0.3)_60%,transparent_100%)]"
          aria-hidden
        />
        <div
          className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_top,rgba(14,14,14,0.9)_0%,rgba(14,14,14,0.55)_45%,rgba(14,14,14,0.42)_100%)]"
          aria-hidden
        />

        <div className="relative flex flex-1 flex-col">
          <div className="flex flex-1 flex-col justify-end px-6 pb-4 pt-10">
            <div>
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <p
                  id="studio-current-session"
                  className="text-[10px] uppercase tracking-[0.12em] text-white/40"
                >
                  Current session
                </p>
                {!isActive ? (
                  <span className="text-[10px] uppercase tracking-[0.08em] text-white/30">· not active</span>
                ) : null}
                {jobRunning ? (
                  <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-[0.08em] text-[#5dcaa5]">
                    <span className="h-1 w-1 animate-pulse rounded-full bg-[#5dcaa5]" />
                    Live
                  </span>
                ) : null}
              </div>
              <h2 className="text-[28px] font-medium leading-none tracking-[-0.5px] text-[#e8e8e8]">
                {bandName || dateLabel}
              </h2>
              {bandName ? (
                <p className="mt-1.5 text-[13px] tabular-nums text-white/55">{dateLabel}</p>
              ) : session.session_key !== dateLabel ? (
                <p className="mt-1 truncate font-mono text-[11px] text-white/30">{session.session_key}</p>
              ) : null}
              {session.venue?.trim() ? (
                <p className="mt-1 truncate text-[11px] text-white/35">{session.venue.trim()}</p>
              ) : null}
            </div>

            <dl className="mt-5 flex flex-wrap gap-x-8 gap-y-2">
              <div className="flex flex-col gap-0.5">
                <dt className="text-[10px] uppercase tracking-[0.08em] text-white/45">Photos</dt>
                <dd className="text-[15px] font-medium tabular-nums text-[#e8e8e8]">
                  {photoCount.toLocaleString("en-US")}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-[10px] uppercase tracking-[0.08em] text-white/45">Processing time</dt>
                <dd className="text-[15px] font-medium tabular-nums text-[#e8e8e8]">
                  {formatElapsedSpaced(elapsed)}
                </dd>
              </div>
              <div className="flex flex-col gap-0.5">
                <dt className="text-[10px] uppercase tracking-[0.08em] text-white/45">Status</dt>
                <dd>
                  <StatusBadge label={jobStatus} running={jobRunning} />
                </dd>
              </div>
            </dl>
          </div>

          {children ? (
            <div className="flex flex-col gap-6 px-6 pb-5 pt-1">{children}</div>
          ) : null}

          <div className="flex flex-wrap items-center justify-end gap-2 px-6 pb-6 pt-2">
            <button
              type="button"
              disabled={busy !== null || analyzeLocked}
              onClick={onAnalyze}
              title="清空本场分析结果并全量重跑 Stage1–3"
              className="h-[30px] rounded-[5px] border border-white/15 bg-white/[0.06] px-3.5 text-xs tracking-[0.03em] text-[#e8e8e8] transition-colors hover:bg-white/[0.1] disabled:opacity-35"
            >
              {busy === "analyze" ? "…" : "全量分析"}
            </button>
            <Link
              href="/gallery"
              className={`inline-flex h-[30px] items-center rounded-[5px] border px-3.5 text-xs tracking-[0.03em] transition-colors ${
                canGallery
                  ? "border-[#e8e8e8] bg-[#e8e8e8] text-[#111] hover:bg-white"
                  : "pointer-events-none border-white/10 bg-white/[0.04] text-white/25"
              }`}
              aria-disabled={!canGallery}
            >
              Open gallery
            </Link>
            {!isActive ? (
              <button
                type="button"
                disabled={busy !== null}
                onClick={onActivate}
                className="h-[30px] rounded-[5px] border border-white/10 px-3 text-xs tracking-[0.03em] text-white/45 transition-colors hover:border-white/20 hover:text-white/70 disabled:opacity-30"
              >
                {busy === "activate" ? "…" : "Set active"}
              </button>
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
