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
      className="landing-hero product-gate relative flex min-h-[100svh] scroll-mt-24 flex-col px-4 pb-16 pt-28 sm:px-8 sm:pb-20 sm:pt-32"
    >
      <div className="landing-hero-glow pointer-events-none absolute inset-0" />
      <div className="landing-hero-grid pointer-events-none absolute inset-0 opacity-[0.35]" />
      <div className="product-gate-grid pointer-events-none absolute inset-0 opacity-[0.22]" />

      <header className="relative z-10 mx-auto w-full max-w-3xl text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.32em] text-white/30">Luma</p>
        <h1 className="mt-3 text-3xl font-light tracking-tight text-white/90 sm:text-4xl">
          Livehouse Photography Agent
        </h1>
        <p className="mx-auto mt-4 max-w-lg text-sm leading-relaxed text-white/40">
          Job-centric 视觉 runtime：入库 → 多阶段分析 → Gallery 选片；Gallery Agent 以
          LangGraph tool-use 挂在真实作业与画廊之上。
        </p>
      </header>

      <div className="relative z-10 mx-auto mt-12 w-full max-w-xl flex-1">
        <Link
          href="/studio"
          onClick={onEnter}
          className="product-portal group relative flex min-h-[22rem] flex-col overflow-hidden rounded-2xl border border-amber-500/15 bg-[#0c0908]/90 p-6 transition-all duration-500 hover:border-amber-400/35 hover:shadow-[0_0_48px_rgba(251,191,36,0.08)] sm:min-h-[24rem] sm:p-8"
        >
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_60%_at_50%_0%,rgba(251,191,36,0.08),transparent_70%)] opacity-0 transition-opacity duration-500 group-hover:opacity-100" />

          <div className="relative flex items-start justify-between gap-3">
            <div>
              <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-amber-200/50">
                Luma Studio
              </p>
              <h2 className="mt-2 text-2xl font-light tracking-tight text-white/92 sm:text-3xl">
                进入工作台
              </h2>
            </div>
            <span className="shrink-0 rounded-full border border-amber-500/25 bg-amber-950/30 px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider text-amber-200/70">
              主线
            </span>
          </div>

          <p className="relative mt-4 max-w-sm text-sm leading-relaxed text-white/45">
            现场摄影入库、OpenCV + VLM 流水线、Gallery 确认导出，以及带 trace 的
            Gallery 对话 Agent。
          </p>

          <ul className="relative mt-6 flex-1 space-y-2.5">
            {[
              "Durable jobs · SQLite SSOT · Celery notify",
              "Stage1→2→3 · 有界推理队列 · model fallback",
              "LangGraph Gallery chat：decide→act→answer + skills",
              "Infra：job timeline · model_runs · eval 基线",
            ].map((f) => (
              <li key={f} className="flex items-start gap-2.5 font-mono text-[11px] text-white/38">
                <span className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-amber-400/70" />
                {f}
              </li>
            ))}
          </ul>

          <div className="relative mt-8 flex items-center justify-between border-t border-amber-500/10 pt-5">
            <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/28">进入 →</span>
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-amber-200/60 transition-colors group-hover:text-amber-100/90">
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
            className="h-3 w-3 rounded border-white/20 bg-transparent accent-amber-500"
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
