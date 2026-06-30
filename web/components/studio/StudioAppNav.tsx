"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { STUDIO_HOME, STUDIO_PRIMARY_NAV, STUDIO_SECONDARY_NAV } from "@/lib/productIa";

function isActive(pathname: string, href: string): boolean {
  if (href === STUDIO_HOME) return pathname === STUDIO_HOME;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function StudioAppNav() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-20 border-b border-white/[0.08] bg-[#0e0e0e]/95 backdrop-blur-sm">
      <div className="flex h-[42px] items-center justify-between gap-4 px-5">
        <Link
          href={STUDIO_HOME}
          className="shrink-0 text-[11px] uppercase tracking-[0.1em] text-white/35 transition-colors hover:text-white/55"
        >
          Luma Studio
        </Link>

        <nav className="flex items-center" aria-label="Studio">
          {STUDIO_PRIMARY_NAV.map((item) => {
            const active = isActive(pathname, item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex h-[42px] items-center border-b-[1.5px] px-3 text-[11px] uppercase tracking-[0.05em] transition-colors ${
                  active
                    ? "border-[#e8e8e8] text-[#e8e8e8]"
                    : "border-transparent text-white/35 hover:text-white/55"
                }`}
                aria-current={active ? "page" : undefined}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex shrink-0 items-center gap-3">
          {STUDIO_SECONDARY_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="hidden text-[11px] uppercase tracking-[0.05em] text-white/30 transition-colors hover:text-white/50 sm:inline"
            >
              {item.label}
            </Link>
          ))}
        </div>
      </div>
    </header>
  );
}
