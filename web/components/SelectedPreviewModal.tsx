"use client";

import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { PreviewMosaicGrid } from "@/components/PreviewMosaicGrid";
import type { GalleryExportItem, GalleryItem } from "@/components/types";
import { gallerySelectionKey } from "@/lib/defaultFilmExport";
import {
  buildExportPreviewUrl,
  exportPreviewLabel,
  resolvePreviewExportSpec,
} from "@/lib/exportPreviewUrl";
import { buildGalleryPlainImageUrl } from "@/lib/galleryDisplayUrl";
const PREVIEW_MAX_SIDE = 1200;

type Props = {
  items: GalleryItem[];
  exportByFile: Record<string, GalleryExportItem>;
  apiBase: string;
  onClose: () => void;
  sessionFilmVariant?: string | null;
  useSessionVibe?: boolean;
  /** ``agent`` = search hits; ``vibe`` = film-grade preview; default = liked selection. */
  variant?: "selection" | "agent" | "vibe";
};

type Row = {
  key: string;
  item: GalleryItem;
  label: string;
  url: string | null;
  index: number;
};

function catalogFallbackKey(item: GalleryItem, index: number): string {
  return item.file?.trim() || item.path?.trim() || `agent-${index}`;
}

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
    const forceSessionVibe = variant === "vibe";
    for (const item of items) {
      const prefKey = gallerySelectionKey(item);
      // Search / shortlist preview must stay ungarded — session vibe is sticky on disk
      // and must not bleed into a fresh "选出吉他手" turn after the user clears chat.
      if (variant === "agent") {
        const url = buildGalleryPlainImageUrl(apiBase, item, PREVIEW_MAX_SIDE);
        if (!url) continue;
        i += 1;
        out.push({
          key: prefKey || catalogFallbackKey(item, i),
          item,
          label: "原图",
          url,
          index: i,
        });
        continue;
      }
      const stored = prefKey ? exportByFile[prefKey] : undefined;
      const spec = resolvePreviewExportSpec(item, stored, {
        sessionFilmVariant,
        useSessionVibe: forceSessionVibe ? true : useSessionVibe,
        forceSessionVibe,
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
  }, [items, exportByFile, apiBase, sessionFilmVariant, useSessionVibe, variant]);

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

  const indexPad = String(Math.max(rows.length, 1)).length;

  return (
    <PreviewModalShell onClose={onClose} variant={variant}>
      <div className="mx-auto w-full max-w-[1280px] px-[clamp(14px,3.5vw,44px)] pb-24 pt-6 md:pt-10">
        {rows.length === 0 ? (
          <div className="rounded-[8px] border border-white/[0.08] bg-white/[0.03] px-4 py-8 text-center">
            <p className="text-[14px] text-white/70">暂无可预览照片</p>
            <p className="mt-2 text-[12px] leading-relaxed text-white/40">
              当前没有可渲染的图片路径。请先在相册里选出几张，或让助手重新筛选后再打开风格预览。
            </p>
            <button
              type="button"
              onClick={onClose}
              className="mt-5 rounded-[6px] border border-white/[0.12] px-3 py-1.5 text-[12px] text-white/70 hover:bg-white/[0.06]"
            >
              关闭
            </button>
          </div>
        ) : (
          <>
            <PreviewIntro count={rows.length} variant={variant} />
            <div className="mt-8 md:mt-12">
              <PreviewMosaicGrid rows={rows} indexPad={indexPad} />
            </div>
            <PreviewFooter onClose={onClose} variant={variant} />
          </>
        )}
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
  variant?: "selection" | "agent" | "vibe";
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  const title =
    variant === "agent" ? "助手筛选预览" : variant === "vibe" ? "胶片风格预览" : "选中图片预览";
  const eyebrow =
    variant === "agent" ? "Agent results" : variant === "vibe" ? "Film vibe" : "Selection review";
  const subtitle =
    variant === "agent"
      ? "助手筛选结果预览"
      : variant === "vibe"
        ? "会话风格成片效果"
        : "导出前效果确认";
  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-[80] flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden text-white outline-none"
      role="dialog"
      aria-modal="false"
      aria-label={title}
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
              {eyebrow}
            </p>
            <p className="mt-1 text-[12px] font-light text-white/45">{subtitle}</p>
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
  variant?: "selection" | "agent" | "vibe";
}) {
  const heading =
    variant === "agent" ? "助手筛选" : variant === "vibe" ? "风格预览" : "预览已选";
  const blurb =
    variant === "agent"
      ? "按检索排序展示命中原图（不套用会话胶片风格）；关闭后可在对话里继续筛选、初选或改风格。"
      : variant === "vibe"
        ? "已套用会话胶片风格；关闭后可在 Lab 微调，或继续对话改风格。"
        : "双列瀑布无框密铺，完整显示成片效果。";
  return (
    <header className="border-b border-white/[0.06] pb-6 md:pb-8">
      <div className="flex flex-wrap items-end justify-between gap-6">
        <div>
          <h1 className="text-[clamp(1.5rem,4.5vw,2.25rem)] font-extralight leading-[1.1] tracking-tight text-white">
            {heading}
          </h1>
          <p className="mt-3 max-w-md text-[13px] font-light leading-relaxed text-white/38">
            {blurb}
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
  variant?: "selection" | "agent" | "vibe";
}) {
  const done =
    variant === "agent"
      ? "已浏览全部筛选结果"
      : variant === "vibe"
        ? "已浏览风格预览"
        : "已浏览全部选中项";
  return (
    <footer className="mt-14 flex flex-col items-center gap-4 border-t border-white/[0.06] pt-10 text-center md:mt-16">
      <p className="text-[11px] font-light tracking-wide text-white/28">{done}</p>
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
