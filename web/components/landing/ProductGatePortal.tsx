"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  productModeHref,
  productModeLabel,
  readProductMode,
  readRememberProductMode,
  saveProductMode,
  type ProductMode,
} from "@/lib/productMode";

type PortalProps = {
  mode: ProductMode;
  title: string;
  subtitle: string;
  description: string;
  features: string[];
  accent: "pro" | "personal";
  badge?: string;
  onEnter: () => void;
};

function PortalCard({
  mode,
  title,
  subtitle,
  description,
  features,
  accent,
  badge,
  onEnter,
}: PortalProps) {
  const isPro = accent === "pro";
  const href = productModeHref(mode);

  return (
    <Link
      href={href}
      onClick={onEnter}
      className={`product-portal group relative flex min-h-[22rem] flex-col overflow-hidden rounded-2xl border p-6 transition-all duration-500 sm:min-h-[26rem] sm:p-8 ${
        isPro
          ? "border-amber-500/15 bg-[#0c0908]/90 hover:border-amber-400/35 hover:shadow-[0_0_48px_rgba(251,191,36,0.08)]"
          : "border-sky-500/15 bg-[#080a0c]/90 hover:border-sky-400/35 hover:shadow-[0_0_48px_rgba(56,189,248,0.08)]"
      }`}
    >
      <div
        className={`pointer-events-none absolute inset-0 opacity-0 transition-opacity duration-500 group-hover:opacity-100 ${
          isPro
            ? "bg-[radial-gradient(ellipse_80%_60%_at_50%_0%,rgba(251,191,36,0.08),transparent_70%)]"
            : "bg-[radial-gradient(ellipse_80%_60%_at_50%_0%,rgba(56,189,248,0.08),transparent_70%)]"
        }`}
      />

      <div className="relative flex items-start justify-between gap-3">
        <div>
          <p
            className={`font-mono text-[10px] uppercase tracking-[0.28em] ${
              isPro ? "text-amber-200/50" : "text-sky-200/50"
            }`}
          >
            {subtitle}
          </p>
          <h2 className="mt-2 text-2xl font-light tracking-tight text-white/92 sm:text-3xl">{title}</h2>
        </div>
        {badge ? (
          <span
            className={`shrink-0 rounded-full border px-2.5 py-1 font-mono text-[9px] uppercase tracking-wider ${
              isPro
                ? "border-amber-500/25 bg-amber-950/30 text-amber-200/70"
                : "border-sky-500/25 bg-sky-950/30 text-sky-200/70"
            }`}
          >
            {badge}
          </span>
        ) : null}
      </div>

      <p className="relative mt-4 max-w-sm text-sm leading-relaxed text-white/45">{description}</p>

      <ul className="relative mt-6 flex-1 space-y-2.5">
        {features.map((f) => (
          <li key={f} className="flex items-start gap-2.5 font-mono text-[11px] text-white/38">
            <span className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${isPro ? "bg-amber-400/70" : "bg-sky-400/70"}`} />
            {f}
          </li>
        ))}
      </ul>

      <div
        className={`relative mt-8 flex items-center justify-between border-t pt-5 ${
          isPro ? "border-amber-500/10" : "border-sky-500/10"
        }`}
      >
        <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-white/28">进入 →</span>
        <span
          className={`font-mono text-[10px] uppercase tracking-[0.16em] transition-colors ${
            isPro ? "text-amber-200/60 group-hover:text-amber-100/90" : "text-sky-200/60 group-hover:text-sky-100/90"
          }`}
        >
          {productModeLabel(mode)}
        </span>
      </div>
    </Link>
  );
}

export function ProductGatePortal() {
  const router = useRouter();
  const [remember, setRemember] = useState(false);
  const [lastMode, setLastMode] = useState<ProductMode | null>(null);
  const autoRedirectDone = useRef(false);

  useEffect(() => {
    const mode = readProductMode();
    const remembered = readRememberProductMode();
    setRemember(remembered);
    setLastMode(mode);
    if (remembered && mode && !autoRedirectDone.current) {
      autoRedirectDone.current = true;
      router.replace(productModeHref(mode));
    }
  }, [router]);

  const onEnter = (mode: ProductMode) => {
    saveProductMode(mode, remember);
  };

  return (
    <section
      id="gate"
      className="landing-hero product-gate relative flex min-h-[100svh] scroll-mt-24 flex-col px-4 pb-16 pt-28 sm:px-8 sm:pb-20 sm:pt-32"
    >
      <div className="landing-hero-glow pointer-events-none absolute inset-0" />
      <div className="landing-hero-grid pointer-events-none absolute inset-0 opacity-[0.35]" />
      <div className="product-gate-grid pointer-events-none absolute inset-0 opacity-[0.22]" />

      <header className="relative z-10 mx-auto w-full max-w-5xl text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.32em] text-white/30">Luma</p>
        <h1 className="mt-3 text-3xl font-light tracking-tight text-white/90 sm:text-4xl">选择你的工作台</h1>
        <p className="mx-auto mt-4 max-w-lg text-sm leading-relaxed text-white/40">
          Luma 摄影助手：专业版面向 Live 摄影全流程；个人版提供轻量图像处理，适合日常修图。
        </p>
      </header>

      <div className="relative z-10 mx-auto mt-12 grid w-full max-w-5xl flex-1 gap-4 sm:mt-14 sm:grid-cols-2 sm:gap-5">
        <PortalCard
          mode="professional"
          title="专业版"
          subtitle="Luma Studio"
          badge="当前主线"
          description="面向现场摄影与选片交付：场次管理、多阶段 AI pipeline、画廊选片、Lab 胶片与导出。"
          features={[
            "SD 入库 · 场次 archive",
            "OpenCV + VLM 多阶段分析",
            "Gallery 选片 · 口味偏好 · 胶片 Lab",
            "Infra 调度与 runtime 控制台",
          ]}
          accent="pro"
          onEnter={() => onEnter("professional")}
        />
        <PortalCard
          mode="personal"
          title="个人版"
          subtitle="Luma Personal"
          badge="即将扩展"
          description="面向个人用户的简单图像处理：上传即修、预设滤镜、基础裁剪与导出，无需完整摄影工作流。"
          features={[
            "单张 / 批量轻量处理",
            "一键增强与风格预设",
            "简单裁剪 · 尺寸 · 格式转换",
            "更少的配置，更快的上手",
          ]}
          accent="personal"
          onEnter={() => onEnter("personal")}
        />
      </div>

      <footer className="relative z-10 mx-auto mt-10 flex w-full max-w-5xl flex-col items-center gap-4 sm:flex-row sm:justify-between">
        <label className="flex cursor-pointer items-center gap-2 font-mono text-[10px] text-white/35">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => {
              const next = e.target.checked;
              setRemember(next);
              const mode = readProductMode();
              if (mode) saveProductMode(mode, next);
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
