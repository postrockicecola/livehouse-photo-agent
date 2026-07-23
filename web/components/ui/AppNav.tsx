"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useState, type ReactNode } from "react";
import {
  APP_MORE_NAV,
  APP_PRIMARY_NAV,
  STUDIO_HOME,
  type NavLink,
} from "@/lib/productIa";

const focusRing =
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/35 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0e0e0e]";

function isActive(pathname: string, href: string): boolean {
  if (href === STUDIO_HOME) return pathname === STUDIO_HOME;
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function NavItem({
  item,
  pathname,
  onNavigate,
}: {
  item: NavLink;
  pathname: string;
  onNavigate?: () => void;
}) {
  const active = isActive(pathname, item.href);
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      className={`flex h-[42px] items-center border-b-[1.5px] px-3 text-[11px] uppercase tracking-[0.05em] transition-colors ${focusRing} ${
        active
          ? "border-[#e8e8e8] text-[#e8e8e8]"
          : "border-transparent text-white/35 hover:text-white/55"
      }`}
      aria-current={active ? "page" : undefined}
    >
      {item.label}
    </Link>
  );
}

type AppNavProps = {
  /** Optional trailing status chip (e.g. Infra health). */
  trailing?: ReactNode;
};

export function AppNav({ trailing }: AppNavProps) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const menuId = useId();

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <header className="sticky top-0 z-40 border-b border-white/[0.08] bg-[#0e0e0e]/95 backdrop-blur-sm">
      <div className="flex h-[42px] items-center justify-between gap-3 px-4 sm:px-5">
        <Link
          href={STUDIO_HOME}
          className={`shrink-0 text-[11px] uppercase tracking-[0.1em] text-white/35 transition-colors hover:text-white/55 ${focusRing}`}
        >
          Luma
        </Link>

        <nav className="hidden items-center md:flex" aria-label="App">
          {APP_PRIMARY_NAV.map((item) => (
            <NavItem key={item.href} item={item} pathname={pathname} />
          ))}
        </nav>

        <div className="flex shrink-0 items-center gap-2 sm:gap-3">
          {trailing}
          <nav className="hidden items-center gap-3 lg:flex" aria-label="More">
            {APP_MORE_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className={`text-[11px] uppercase tracking-[0.05em] text-white/30 transition-colors hover:text-white/50 ${focusRing}`}
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <button
            type="button"
            className={`inline-flex h-8 w-8 items-center justify-center rounded-md border border-white/[0.1] text-white/55 transition-colors hover:text-white/80 md:hidden ${focusRing}`}
            aria-expanded={open}
            aria-controls={menuId}
            aria-label={open ? "关闭菜单" : "打开菜单"}
            onClick={() => setOpen((v) => !v)}
          >
            <span aria-hidden className="font-mono text-[12px]">
              {open ? "×" : "≡"}
            </span>
          </button>
        </div>
      </div>

      {open ? (
        <div
          id={menuId}
          className="border-t border-white/[0.06] bg-[#0e0e0e] px-4 py-3 md:hidden"
        >
          <nav className="flex flex-col gap-1" aria-label="Mobile app">
            {[...APP_PRIMARY_NAV, ...APP_MORE_NAV].map((item) => {
              const active = isActive(pathname, item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={() => setOpen(false)}
                  className={`rounded-md px-3 py-2.5 text-[12px] uppercase tracking-[0.06em] transition-colors ${focusRing} ${
                    active ? "bg-white/[0.08] text-white" : "text-white/45 hover:bg-white/[0.04] hover:text-white/70"
                  }`}
                  aria-current={active ? "page" : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </div>
      ) : null}
    </header>
  );
}

/** @deprecated Use AppNav — kept as alias for gradual migration. */
export function StudioAppNav() {
  return <AppNav />;
}
