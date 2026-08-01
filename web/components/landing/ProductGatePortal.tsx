"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  productModeHref,
  productModeLabel,
  readProductMode,
  readRememberProductMode,
  saveProductMode,
} from "@/lib/productMode";

/** Single product entry (Professional / Studio + Agent). */
export function ProductGatePortal() {
  const router = useRouter();
  const [remember, setRemember] = useState(false);
  const [lastMode, setLastMode] = useState<"professional" | null>(null);

  useEffect(() => {
    const mode = readProductMode();
    setRemember(readRememberProductMode());
    setLastMode(mode);
  }, []);

  const onEnter = () => {
    saveProductMode("professional", remember);
  };

  return (
    <section
      id="gate"
      className="studio-grain product-gate relative flex min-h-[100svh] scroll-mt-24 flex-col px-4 pb-16 pt-28 sm:px-8 sm:pb-20 sm:pt-32"
    >
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_70%_45%_at_50%_20%,rgba(255,244,230,0.04),transparent_70%)]" />

      <header className="relative z-10 mx-auto w-full max-w-3xl text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.32em] text-[rgba(255,244,230,0.35)]">
          Luma
        </p>
        <h1 className="mt-3 text-3xl font-light tracking-tight text-[rgba(247,244,240,0.92)] sm:text-4xl">
          进暗房，打开本场胶卷
        </h1>
        <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-white/40">
          入库与多阶段分析把场次算完；Gallery 里确认样张、试胶片风格、用对话 Agent 选片导出。
        </p>
      </header>

      <div className="relative z-10 mx-auto mt-12 w-full max-w-xl flex-1">
        <Link
          href="/studio"
          onClick={onEnter}
          className="product-portal group relative flex min-h-[20rem] flex-col overflow-hidden border border-[var(--luma-matte)] bg-[rgba(12,11,10,0.92)] p-6 transition-[border-color,background-color] duration-400 hover:border-[var(--luma-matte-strong)] hover:bg-[rgba(14,13,12,0.96)] sm:min-h-[22rem] sm:p-8"
        >
          <div className="relative flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-[rgba(255,244,230,0.4)]">
                Luma Studio
              </p>
              <h2 className="mt-2 text-2xl font-light tracking-tight text-[rgba(247,244,240,0.92)] sm:text-3xl">
                进入工作台
              </h2>
            </div>
            <span className="shrink-0 border border-[var(--luma-matte)] px-2.5 py-1 font-mono text-[9px] uppercase tracking-[0.16em] text-[rgba(255,244,230,0.55)]">
              Darkroom
            </span>
          </div>

          <p className="relative mt-4 max-w-sm text-sm leading-relaxed text-white/42">
            现场摄影入库 → 样张筛选 → Gallery 确认与胶片风格 → 预览 / RAW 导出。
          </p>

          <ul className="relative mt-6 flex-1 space-y-2.5">
            {[
              "场次入库与预览抽取",
              "多阶段分析 · 可恢复作业",
              "Gallery 选片 · 胶片预览 · Agent",
              "Infra 作业时间线（需要时再看）",
            ].map((f) => (
              <li
                key={f}
                className="flex items-start gap-2.5 font-mono text-[11px] text-white/38"
              >
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-[rgba(255,244,230,0.55)]" />
                {f}
              </li>
            ))}
          </ul>

          <div className="relative mt-8 flex items-center justify-between border-t border-[var(--luma-stroke)] pt-5">
            <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/28">
              进入 →
            </span>
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-[rgba(255,244,230,0.5)] transition-colors group-hover:text-[rgba(255,244,230,0.85)]">
              Studio
            </span>
          </div>
        </Link>
      </div>

      <footer className="relative z-10 mx-auto mt-10 flex w-full max-w-xl flex-col items-center gap-4 sm:flex-row sm:justify-between">
        <label className="flex cursor-pointer items-center gap-2 font-mono text-[10px] text-white/35">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => {
              const next = e.target.checked;
              setRemember(next);
              saveProductMode("professional", next);
            }}
            className="h-3 w-3 rounded-sm border-white/20 bg-transparent accent-[rgba(255,236,210,0.85)]"
          />
          记住选择，下次直接进入
        </label>
        {lastMode ? (
          <button
            type="button"
            onClick={() => router.push(productModeHref(lastMode))}
            className="font-mono text-[10px] text-white/30 transition-colors hover:text-white/55"
          >
            上次使用 · {productModeLabel(lastMode)} →
          </button>
        ) : null}
      </footer>
    </section>
  );
}
