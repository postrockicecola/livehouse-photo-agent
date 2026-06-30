"use client";

import Link from "next/link";
import type { PersonalFeature } from "@/lib/personalFeatures";
import { PersonalChrome } from "@/components/personal/PersonalChrome";

type Props = {
  feature: PersonalFeature;
};

export function PersonalComingSoon({ feature }: Props) {
  return (
    <main className="studio-grain relative flex min-h-screen flex-col px-4 py-10 sm:px-8">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(56,189,248,0.08),transparent)]" />
      <PersonalChrome title={feature.title} />
      <section className="relative z-10 mx-auto mt-10 w-full max-w-lg">
        <div className="rounded-2xl border border-white/10 bg-[#080a0c]/80 p-8 text-center">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/35">筹备中</p>
          <p className="mt-4 text-sm leading-relaxed text-white/55">{feature.description}</p>
          <p className="mt-6 text-xs text-white/30">入口已预留，后续版本会在这里接入完整流程。</p>
          <Link
            href="/personal"
            className="mt-8 inline-block rounded-full border border-sky-500/30 px-5 py-2 font-mono text-[10px] uppercase tracking-[0.2em] text-sky-200/80 transition-colors hover:border-sky-400/50 hover:text-sky-100"
          >
            返回功能列表
          </Link>
        </div>
      </section>
    </main>
  );
}
