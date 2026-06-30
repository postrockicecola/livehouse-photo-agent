"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { LANDING_NAV, LANDING_STUDIO_CTA, STUDIO_HOME } from "@/lib/productIa";

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={`landing-nav fixed inset-x-0 top-0 z-50 transition-[background,border-color,backdrop-filter] duration-300 ${
        scrolled ? "landing-nav--scrolled border-b border-white/[0.06] bg-[#0a0a0a]/80 backdrop-blur-xl" : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[104rem] items-center justify-between gap-6 px-5 sm:h-[4.25rem] sm:px-8 lg:px-12">
        <Link href="/" className="group flex items-center gap-2.5">
          <span className="font-mono text-[11px] uppercase tracking-[0.32em] text-white/88 transition-colors group-hover:text-white">
            Luma Studio
          </span>
        </Link>

        <nav className="hidden items-center gap-8 md:flex" aria-label="Marketing">
          {LANDING_NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/42 transition-colors hover:text-white/78"
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-2.5 sm:gap-3">
          <Link
            href={STUDIO_HOME}
            className="rounded-full bg-white px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-[#0a0a0a] transition-opacity hover:opacity-90 sm:px-5"
          >
            {LANDING_STUDIO_CTA}
          </Link>
        </div>
      </div>
    </header>
  );
}
