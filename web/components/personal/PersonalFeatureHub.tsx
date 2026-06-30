"use client";

import Link from "next/link";
import { PERSONAL_FEATURES } from "@/lib/personalFeatures";

export function PersonalFeatureHub() {
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {PERSONAL_FEATURES.map((f) => {
        const card = (
          <article
            className={`group relative flex min-h-[10.5rem] flex-col rounded-2xl border p-5 transition-all duration-300 sm:p-6 ${
              f.available
                ? "border-sky-500/25 bg-[#080a0c]/90 hover:border-sky-400/45 hover:shadow-[0_0_32px_rgba(56,189,248,0.1)]"
                : "border-white/8 bg-[#080a0c]/60 hover:border-white/14"
            }`}
          >
            <div className="pointer-events-none absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-300 group-hover:opacity-100 bg-[radial-gradient(ellipse_80%_50%_at_50%_0%,rgba(56,189,248,0.06),transparent_70%)]" />
            <div className="relative flex items-start justify-between gap-2">
              <h2 className="text-lg font-light tracking-tight text-white/92">{f.title}</h2>
              <span
                className={`shrink-0 rounded-full border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider ${
                  f.available
                    ? "border-sky-500/30 bg-sky-950/40 text-sky-200/80"
                    : "border-white/10 bg-white/5 text-white/35"
                }`}
              >
                {f.badge ?? (f.available ? "可用" : "筹备中")}
              </span>
            </div>
            <p className="relative mt-3 flex-1 text-sm leading-relaxed text-white/45">{f.description}</p>
            <p
              className={`relative mt-4 font-mono text-[10px] uppercase tracking-[0.2em] ${
                f.available ? "text-sky-300/70 group-hover:text-sky-200/90" : "text-white/30"
              }`}
            >
              {f.available ? "进入 →" : "查看说明 →"}
            </p>
          </article>
        );

        return (
          <Link key={f.slug} href={f.href} className="block focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/50 rounded-2xl">
            {card}
          </Link>
        );
      })}
    </div>
  );
}
