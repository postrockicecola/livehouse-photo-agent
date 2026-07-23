"use client";

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { LANDING_NAV, LANDING_STUDIO_CTA, STUDIO_HOME } from "@/lib/productIa";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0a0a0a]";

export function LandingNav() {
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);
  const menuId = useId();

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header
      className={`landing-nav fixed inset-x-0 top-0 z-50 transition-[background,border-color,backdrop-filter] duration-300 ${
        scrolled
          ? "landing-nav--scrolled border-b border-white/[0.06] bg-[#0a0a0a]/80 backdrop-blur-xl"
          : "border-b border-transparent bg-transparent"
      }`}
    >
      <div className="mx-auto flex h-16 max-w-[104rem] items-center justify-between gap-6 px-5 sm:h-[4.25rem] sm:px-8 lg:px-12">
        <Link href="/" className={`group flex items-center gap-2.5 ${focusRing}`}>
          <span className="font-mono text-[11px] uppercase tracking-[0.32em] text-white/88 transition-colors group-hover:text-white">
            Luma
          </span>
        </Link>

        <nav className="hidden items-center gap-8 md:flex" aria-label="Marketing">
          {LANDING_NAV.map((item) => (
            <a
              key={item.href}
              href={item.href}
              className={`font-mono text-[10px] uppercase tracking-[0.18em] text-white/42 transition-colors hover:text-white/78 ${focusRing}`}
            >
              {item.label}
            </a>
          ))}
        </nav>

        <div className="flex items-center gap-2.5 sm:gap-3">
          <Link
            href={STUDIO_HOME}
            className={`rounded-full bg-white px-4 py-2 font-mono text-[10px] uppercase tracking-[0.14em] text-[#0a0a0a] transition-opacity hover:opacity-90 sm:px-5 ${focusRing}`}
          >
            {LANDING_STUDIO_CTA}
          </Link>
          <button
            type="button"
            className={`inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/15 text-white/60 transition-colors hover:text-white md:hidden ${focusRing}`}
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? "关闭菜单" : "打开菜单"}
            onClick={() => setOpen((v) => !v)}
          >
            <span aria-hidden className="font-mono text-[13px]">
              {open ? "×" : "≡"}
            </span>
          </button>
        </div>
      </div>

      {open ? (
        <div id={menuId} className="border-t border-white/[0.06] bg-[#0a0a0a]/95 px-5 py-4 backdrop-blur-xl md:hidden">
          <nav className="flex flex-col gap-1" aria-label="Mobile marketing">
            {LANDING_NAV.map((item) => (
              <a
                key={item.href}
                href={item.href}
                onClick={() => setOpen(false)}
                className={`rounded-md px-3 py-2.5 font-mono text-[11px] uppercase tracking-[0.14em] text-white/55 transition-colors hover:bg-white/[0.04] hover:text-white/85 ${focusRing}`}
              >
                {item.label}
              </a>
            ))}
          </nav>
        </div>
      ) : null}
    </header>
  );
}
