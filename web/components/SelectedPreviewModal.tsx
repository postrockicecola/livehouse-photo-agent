"use client";

import { useEffect, useMemo, type ReactNode } from "react";
import { PreviewMosaicGrid } from "@/components/PreviewMosaicGrid";
import type { GalleryExportItem, GalleryItem } from "@/components/types";
import { gallerySelectionKey } from "@/lib/defaultFilmExport";
import {
  buildExportPreviewUrl,
  exportPreviewLabel,
  resolvePreviewExportSpec,
} from "@/lib/exportPreviewUrl";

const PREVIEW_MAX_SIDE = 1200;

type Props = {
  items: GalleryItem[];
  exportByFile: Record<string, GalleryExportItem>;
  apiBase: string;
  onClose: () => void;
  sessionFilmVariant?: string | null;
  useSessionVibe?: boolean;
  /** ``agent`` = copilot search hits; default = liked selection review. */
  variant?: "selection" | "agent";
};

type Row = {
  key: string;
  item: GalleryItem;
  label: string;
  url: string | null;
  index: number;
};

export function SelectedPreviewModal({
  items,
  exportByFile,
  apiBase,
  onClose,
  sessionFilmVariant,
  useSessionVibe,
  variant = "selection",
}: Props) {
  const rows = useMemo((): Row[] => {
    const out: Row[] = [];
    let i = 0;
    for (const item of items) {
      const prefKey = gallerySelectionKey(item);
      const stored = prefKey ? exportByFile[prefKey] : undefined;
      const spec = resolvePreviewExportSpec(item, stored, {
        sessionFilmVariant,
        useSessionVibe,
      });
      if (!spec) continue;
      i += 1;
      const key = prefKey || spec.file;
      out.push({
        key,
        item,
        label: exportPreviewLabel(spec),
        url: buildExportPreviewUrl(apiBase, spec, PREVIEW_MAX_SIDE),
        index: i,
      });
    }
    return out;
  }, [items, exportByFile, apiBase, sessionFilmVariant, useSessionVibe]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  if (rows.length === 0) return null;

  const indexPad = String(rows.length).length;

  return (
    <PreviewModalShell onClose={onClose} variant={variant}>
      <div className="mx-auto w-full max-w-[1280px] px-[clamp(14px,3.5vw,44px)] pb-24 pt-6 md:pt-10">
        <PreviewIntro count={rows.length} variant={variant} />
        <div className="mt-8 md:mt-12">
          <PreviewMosaicGrid rows={rows} indexPad={indexPad} />
        </div>
        <PreviewFooter onClose={onClose} variant={variant} />
      </div>
    </PreviewModalShell>
  );
}

function PreviewModalShell({
  onClose,
  children,
  variant = "selection",
}: {
  onClose: () => void;
  children: ReactNode;
  variant?: "selection" | "agent";
}) {
  const isAgent = variant === "agent";
  return (
    <div
      className="fixed inset-0 z-[55] flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden font-[system-ui,-apple-system,sans-serif] text-white"
      role="dialog"
      aria-modal="true"
      aria-label={isAgent ? "助手筛选预览" : "选中图片预览"}
    >
      <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]" aria-hidden />
      <div
        className="pointer-events-none absolute inset-x-0 top-0 h-[min(42vh,420px)] bg-[radial-gradient(ellipse_80%_60%_at_50%_-20%,rgba(255,255,255,0.06),transparent)]"
        aria-hidden
      />
      <header className="relative z-10 shrink-0 border-b border-white/[0.06] bg-[#0a0a0a]/75 backdrop-blur-xl backdrop-saturate-150">
        <div className="mx-auto flex max-w-[1280px] items-center justify-between gap-4 px-[clamp(14px,3.5vw,44px)] py-4 md:py-5">
          <div className="min-w-0">
            <p className="text-[10px] font-light uppercase tracking-[0.22em] text-white/32">
              {isAgent ? "Agent results" : "Selection review"}
            </p>
            <p className="mt-1 text-[12px] font-light text-white/45">
              {isAgent ? "助手筛选结果预览" : "导出前效果确认"}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex shrink-0 items-center gap-2 rounded-[8px] border border-white/[0.12] bg-black/40 px-3.5 py-2 text-[11px] font-normal tracking-wide text-white/80 shadow-[0_8px_32px_rgba(0,0,0,0.35)] backdrop-blur-md transition-colors hover:border-white/[0.2] hover:bg-black/55 hover:text-white"
          >
            <span className="text-[15px] leading-none text-white/55" aria-hidden>
              ×
            </span>
            返回相册
            <span className="hidden text-[10px] text-white/30 sm:inline">Esc</span>
          </button>
        </div>
      </header>
      <main className="relative z-[1] min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</main>
    </div>
  );
}

function PreviewIntro({
  count,
  variant = "selection",
}: {
  count: number;
  variant?: "selection" | "agent";
}) {
  const isAgent = variant === "agent";
  return (
    <header className="border-b border-white/[0.06] pb-6 md:pb-8">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <h1 className="text-[clamp(1.5rem,4.5vw,2.25rem)] font-extralight leading-[1.1] tracking-tight text-white">
            {isAgent ? "助手筛选" : "预览已选"}
          </h1>
          <p className="mt-3 max-w-md text-[13px] font-light leading-relaxed text-white/38">
            {isAgent
              ? "按检索排序展示命中照片；关闭后可在对话里继续筛选或初选。"
              : "双列瀑布无框密铺，完整显示成片效果。"}
          </p>
        </div>
        <div className="flex items-baseline gap-2 tabular-nums">
          <span className="text-[clamp(2.5rem,8vw,3.75rem)] font-extralight leading-none text-white/90">
            {count}
          </span>
          <span className="pb-1 text-[11px] font-light uppercase tracking-[0.16em] text-white/30">photos</span>
        </div>
      </div>
    </header>
  );
}

function PreviewFooter({
  onClose,
  variant = "selection",
}: {
  onClose: () => void;
  variant?: "selection" | "agent";
}) {
  const isAgent = variant === "agent";
  return (
    <footer className="mt-14 flex flex-col items-center gap-4 border-t border-white/[0.06] pt-10 text-center md:mt-16">
      <p className="text-[11px] font-light tracking-wide text-white/28">
        {isAgent ? "已浏览全部筛选结果" : "已浏览全部选中项"}
      </p>
      <button
        type="button"
        onClick={onClose}
        className="rounded-[6px] border border-white/[0.1] bg-white/[0.05] px-6 py-2.5 text-[11px] font-normal tracking-[0.12em] text-white/70 transition-colors hover:bg-white/[0.09] hover:text-white/90"
      >
        返回相册继续编辑
      </button>
    </footer>
  );
}
