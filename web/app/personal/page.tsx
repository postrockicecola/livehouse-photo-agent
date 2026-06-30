"use client";

import Link from "next/link";
import { PersonalChrome } from "@/components/personal/PersonalChrome";
import { PersonalFeatureHub } from "@/components/personal/PersonalFeatureHub";

export default function PersonalPage() {
  return (
    <main className="studio-grain relative flex min-h-screen flex-col px-4 py-10 sm:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(56,189,248,0.08),transparent)]" />

      <PersonalChrome title="个人版" subtitle="选择功能" />

      <section className="relative z-10 mx-auto mt-10 w-full max-w-4xl flex-1">
        <p className="mb-6 max-w-xl text-sm leading-relaxed text-white/45">
          轻量图像工具集合。点击下方卡片进入对应功能；标记为「筹备中」的入口已预留，后续可逐项接入。
        </p>
        <PersonalFeatureHub />

        <p className="mt-10 text-center font-mono text-[10px] text-white/25">
          需要完整选片流程？{" "}
          <Link href="/studio" className="text-sky-300/60 hover:text-sky-200/80">
            进入 Luma Studio
          </Link>
        </p>
      </section>
    </main>
  );
}
