"use client";

import { useEffect, useRef } from "react";
import { useFocusTrap } from "@/lib/useFocusTrap";

export type ShowcasePreviewItem = {
  path: string;
  file?: string;
  score?: number;
};

type Props = {
  items: ShowcasePreviewItem[];
  onClose: () => void;
  /** ``vibe`` applies a CSS film look (no server-side grade). */
  variant?: "agent" | "vibe";
  filmLabel?: string;
  /** CSS grade class from showcase session_vibe.grade_class */
  gradeClass?: string;
};

/**
 * Full-screen preview for Showcase Agent results (static /showcase paths).
 * Works on Studio without Gallery / gallery_server.
 */
export function ShowcasePreviewModal({
  items,
  onClose,
  variant = "agent",
  filmLabel,
  gradeClass = "showcase-grade-cinestill",
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useFocusTrap(true, dialogRef);

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

  if (!items.length) return null;

  const title = variant === "vibe" ? "风格预览" : "助手筛选预览";
  const eyebrow = variant === "vibe" ? "Style vibe · Showcase" : "Agent results · Showcase";
  const subtitle =
    variant === "vibe"
      ? filmLabel || "风格模拟（CSS grade，非光学渲染）"
      : "2026-04-16 预录 keepers";
  const isDreamcore = gradeClass.includes("dreamcore");

  return (
    <div
      ref={dialogRef}
      tabIndex={-1}
      className="fixed inset-0 z-[60] flex h-[100dvh] max-h-[100dvh] flex-col overflow-hidden text-white outline-none"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="pointer-events-none absolute inset-0 bg-[#0a0a0a]" aria-hidden />
      <header className="relative z-10 shrink-0 border-b border-white/[0.06] bg-[#0a0a0a]/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1280px] items-center justify-between gap-4 px-4 py-4 sm:px-8">
          <div className="min-w-0">
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/32">{eyebrow}</p>
            <p className="mt-1 truncate text-[12px] text-white/45">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex shrink-0 items-center gap-2 rounded-[8px] border border-white/[0.12] bg-black/40 px-3.5 py-2 text-[11px] text-white/80 transition-colors hover:bg-black/55 hover:text-white"
          >
            <span aria-hidden>×</span>
            关闭
            <span className="hidden text-[10px] text-white/30 sm:inline">Esc</span>
          </button>
        </div>
      </header>

      <main className="relative z-[1] min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="mx-auto w-full max-w-[1280px] px-4 pb-24 pt-6 sm:px-8 md:pt-10">
          <header className="border-b border-white/[0.06] pb-6 md:pb-8">
            <div className="flex flex-wrap items-end justify-between gap-6">
              <div>
                <h1 className="text-[clamp(1.5rem,4.5vw,2.25rem)] font-extralight tracking-tight text-white">
                  {variant === "vibe" ? "风格预览" : "助手筛选"}
                </h1>
                <p className="mt-3 max-w-md text-[13px] font-light leading-relaxed text-white/38">
                  {variant === "vibe"
                    ? "Showcase 用 CSS 近似胶片观感；本地 Gallery 可跑真实光学 / Lab 微调。"
                    : "预录选片结果全屏浏览；关闭后可继续在对话里换指令。"}
                </p>
              </div>
              <div className="flex items-baseline gap-2 tabular-nums">
                <span className="text-[clamp(2.5rem,8vw,3.75rem)] font-extralight leading-none text-white/90">
                  {items.length}
                </span>
                <span className="pb-1 text-[11px] uppercase tracking-[0.16em] text-white/30">photos</span>
              </div>
            </div>
          </header>

          <div className="mt-8 columns-2 gap-3 md:mt-12 md:columns-3 md:gap-4 lg:columns-4">
            {items.map((it, i) => (
              <figure
                key={it.path}
                className={[
                  "mb-3 break-inside-avoid overflow-hidden rounded-[6px] border border-white/[0.06] bg-black/40 md:mb-4",
                  variant === "vibe" && isDreamcore ? "showcase-grade-dreamcore-wrap" : "",
                ].join(" ")}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={it.path}
                  alt=""
                  className={[
                    "w-full object-cover",
                    variant === "vibe" ? gradeClass : "",
                  ].join(" ")}
                />
                <figcaption className="relative z-[1] flex items-center justify-between gap-2 px-2.5 py-2 font-mono text-[10px] text-white/40">
                  <span className="truncate">{it.file || it.path.split("/").pop()}</span>
                  <span className="shrink-0 tabular-nums text-white/28">
                    {String(i + 1).padStart(2, "0")}
                    {it.score != null ? ` · ${Number(it.score).toFixed(1)}` : ""}
                  </span>
                </figcaption>
              </figure>
            ))}
          </div>

          <footer className="mt-14 flex flex-col items-center gap-4 border-t border-white/[0.06] pt-10 text-center">
            <p className="text-[11px] text-white/28">
              {variant === "vibe" ? "已浏览风格预览" : "已浏览全部筛选结果"}
            </p>
            <button
              type="button"
              onClick={onClose}
              className="rounded-[6px] border border-white/[0.1] bg-white/[0.05] px-6 py-2.5 text-[11px] tracking-[0.12em] text-white/70 transition-colors hover:bg-white/[0.09]"
            >
              返回对话
            </button>
          </footer>
        </div>
      </main>
    </div>
  );
}
