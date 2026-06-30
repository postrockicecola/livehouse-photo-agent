import Link from "next/link";
import { LANDING_DOC_LINKS } from "@/lib/productIa";

export function LandingDocsSection() {
  return (
    <section id="docs" className="landing-section scroll-mt-24 border-t border-white/[0.05] py-20 sm:py-28">
      <div className="mx-auto w-full max-w-[104rem] px-5 sm:px-8 lg:px-12">
        <header className="max-w-2xl">
          <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">04 · Docs</p>
          <h2 className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl">文档与上手</h2>
          <p className="mt-4 text-sm leading-relaxed text-white/38 sm:text-base">
            配置、pipeline 与运维细节集中在这里；想直接看效果，进入 Studio 实操即可。
          </p>
        </header>

        <ul className="mt-12 grid gap-3 sm:mt-16 sm:grid-cols-2">
          {LANDING_DOC_LINKS.map((item) => (
            <li key={item.label}>
              <Link
                href={item.href}
                className="group flex flex-col rounded-2xl border border-white/[0.07] bg-white/[0.02] px-5 py-4 transition-colors hover:border-white/[0.12] hover:bg-white/[0.035]"
              >
                <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-white/45 transition-colors group-hover:text-white/68">
                  {item.label}
                </span>
                {item.description ? (
                  <span className="mt-2 text-sm leading-relaxed text-white/32">{item.description}</span>
                ) : null}
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
