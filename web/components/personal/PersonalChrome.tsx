"use client";

import Link from "next/link";
import { saveProductMode } from "@/lib/productMode";

type PersonalChromeProps = {
  title: string;
  subtitle?: string;
  backHref?: string;
  backLabel?: string;
};

export function PersonalChrome({
  title,
  subtitle = "Luma Personal",
  backHref = "/personal",
  backLabel = "功能列表",
}: PersonalChromeProps) {
  return (
    <header className="relative z-10 mx-auto flex w-full max-w-4xl items-start justify-between gap-4">
      <div>
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-sky-200/40">{subtitle}</p>
        <h1 className="mt-1 text-2xl font-light tracking-tight text-white/90 sm:text-3xl">{title}</h1>
      </div>
      <nav className="flex flex-wrap items-center justify-end gap-3 font-mono text-[10px] uppercase tracking-[0.18em]">
        <Link href={backHref} className="text-white/45 transition-colors hover:text-white/75">
          ← {backLabel}
        </Link>
        <Link
          href="/"
          className="text-white/35 transition-colors hover:text-white/60"
          onClick={() => saveProductMode("personal", false)}
        >
          切换版本
        </Link>
        <Link href="/studio" className="text-white/45 transition-colors hover:text-white/75">
          专业版 →
        </Link>
      </nav>
    </header>
  );
}
