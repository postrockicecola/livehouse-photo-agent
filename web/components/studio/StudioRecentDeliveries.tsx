"use client";

import { useId, useState } from "react";
import type { StudioRecentDelivery } from "@/lib/studioApi";
import { formatDeliveryPhotos } from "@/lib/studioUi";

type Props = {
  deliveries: StudioRecentDelivery[];
  loading?: boolean;
  selectedPreviewsDir?: string;
  onSelectSession?: (previewsDir: string) => void;
  collapsible?: boolean;
  defaultCollapsed?: boolean;
};

export function StudioRecentDeliveries({
  deliveries,
  loading,
  selectedPreviewsDir,
  onSelectSession,
  collapsible = false,
  defaultCollapsed = false,
}: Props) {
  const [open, setOpen] = useState(!defaultCollapsed);
  const panelId = useId();

  const countLabel = deliveries.length > 0 ? `${deliveries.length}` : "0";

  return (
    <section className="recent-deliveries w-full" aria-label="Recent deliveries">
      <div className="mb-2 flex items-center justify-between gap-2">
        {collapsible ? (
          <button
            type="button"
            className="group flex min-w-0 flex-1 items-center gap-2 text-left"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((v) => !v)}
          >
            <p className="text-[10px] uppercase tracking-[0.1em] text-white/30">Recent deliveries</p>
            <span className="text-[10px] tabular-nums text-white/20">{countLabel}</span>
            <span
              className={`ml-auto shrink-0 text-white/25 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
              aria-hidden
            >
              ▾
            </span>
          </button>
        ) : (
          <p className="text-[10px] uppercase tracking-[0.1em] text-white/30">Recent deliveries</p>
        )}
      </div>

      {(!collapsible || open) && (
        <ol id={panelId} className="relative space-y-0 rounded-lg border border-white/[0.06] bg-[#161616] px-3 py-2">
          {loading && deliveries.length === 0 ? (
            <li className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-8 animate-pulse rounded bg-white/[0.03]" />
              ))}
            </li>
          ) : null}

          {!loading && deliveries.length === 0 ? (
            <li className="py-1.5 text-[11px] leading-relaxed text-white/28">
              Completed exports will appear here once sessions have analysis results.
            </li>
          ) : null}

          {deliveries.map((row, idx) => {
            const isLast = idx === deliveries.length - 1;
            const active = selectedPreviewsDir != null && row.previews_dir === selectedPreviewsDir;
            const content = (
              <>
                <span className="relative z-[1] mt-0.5 shrink-0 text-xs text-[#5dcaa5]/70" aria-hidden>
                  ✓
                </span>
                <div className="min-w-0 flex-1">
                  <p className={`text-[13px] tabular-nums tracking-tight ${active ? "text-white/88" : "text-white/65"}`}>
                    {row.session_date}
                  </p>
                  <p className="mt-0.5 text-[10px] tabular-nums text-white/35">
                    {formatDeliveryPhotos(row)}
                  </p>
                </div>
              </>
            );

            return (
              <li
                key={`${row.previews_dir}-${row.session_date}`}
                className={`relative flex gap-2.5 ${isLast ? "pb-0" : "pb-4"}`}
              >
                {!isLast ? (
                  <span
                    className="absolute left-[5px] top-4 h-[calc(100%-6px)] w-px bg-white/[0.06]"
                    aria-hidden
                  />
                ) : null}

                {onSelectSession ? (
                  <button
                    type="button"
                    onClick={() => onSelectSession(row.previews_dir)}
                    className={`relative flex w-full gap-2.5 rounded-md text-left transition-colors hover:text-white/90 ${
                      active ? "text-white/90" : ""
                    } -mx-0.5 px-0.5 py-0.5`}
                  >
                    {content}
                  </button>
                ) : (
                  <div className="relative flex gap-2.5">{content}</div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
