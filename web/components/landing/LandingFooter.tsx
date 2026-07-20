import Link from "next/link";
import { LANDING_FOOTER_COLUMNS } from "@/lib/productIa";

export function LandingFooter() {
  return (
    <footer className="border-t border-white/[0.06] py-16 sm:py-20">
      <div className="mx-auto w-full max-w-[104rem] px-5 sm:px-8 lg:px-12">
        <div className="grid gap-12 sm:grid-cols-2 lg:grid-cols-5">
          <div className="lg:col-span-1">
            <p className="font-mono text-[11px] uppercase tracking-[0.32em] text-white/75">Luma Studio</p>
            <p className="mt-4 max-w-xs text-sm leading-relaxed text-white/32">
              个人项目：视觉处理 pipeline、作业 Infra、选片 Agent。用 Live 摄影场次跑真实数据。
            </p>
          </div>

          {LANDING_FOOTER_COLUMNS.map((col) => (
            <div key={col.title}>
              <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-white/38">{col.title}</p>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((link) => (
                  <li key={link.label}>
                    {link.href.startsWith("#") || link.href.startsWith("/") ? (
                      <Link
                        href={link.href}
                        className={`text-sm transition-colors ${
                          col.title === "Operators"
                            ? "text-white/30 hover:text-white/52"
                            : "text-white/42 hover:text-white/72"
                        }`}
                      >
                        {link.label}
                      </Link>
                    ) : (
                      <span className="text-sm text-white/42">{link.label}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-16 flex flex-col gap-3 border-t border-white/[0.06] pt-8 sm:flex-row sm:items-center sm:justify-between">
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/24">
            © {new Date().getFullYear()} Luma Studio
          </p>
          <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/24">AI fullstack · Infra · Agent</p>
        </div>
      </div>
    </footer>
  );
}
