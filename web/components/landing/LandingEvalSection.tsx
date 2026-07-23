import Link from "next/link";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { STAGE3_HEADLINE, STRATEGY_ROWS } from "@/lib/evalShowcase";

export function LandingEvalSection() {
  return (
    <section
      id="evaluation"
      className="landing-section scroll-mt-24 border-t border-white/[0.05] py-20 sm:py-24"
      aria-labelledby="landing-eval-title"
    >
      <div className="mx-auto w-full max-w-[104rem] px-5 sm:px-8 lg:px-12">
        <header className="max-w-2xl">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">Evaluation</p>
            <ProvenanceBadge kind="recorded" />
          </div>
          <h2
            id="landing-eval-title"
            className="mt-4 text-3xl font-light tracking-tight text-white/[0.9] sm:text-4xl"
          >
            固定评估集上的对比。
          </h2>
          <p className="mt-4 text-sm leading-relaxed text-white/38 sm:text-base">
            250 张人工标注。Stage3 Spearman {STAGE3_HEADLINE.spearman} · MAE {STAGE3_HEADLINE.mae}。完整表、Agent
            基线与报告路径见 Evaluation。
          </p>
        </header>

        <div className="mt-10 overflow-x-auto rounded-xl border border-white/[0.08]">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-white/[0.08] bg-white/[0.03] font-mono text-[10px] uppercase tracking-[0.12em] text-white/35">
              <tr>
                <th className="px-3 py-2.5">策略</th>
                <th className="px-3 py-2.5">质量</th>
                <th className="px-3 py-2.5">VLM 调用</th>
                <th className="px-3 py-2.5">标签</th>
              </tr>
            </thead>
            <tbody className="text-white/60">
              {STRATEGY_ROWS.map((row) => (
                <tr key={row.id} className="border-b border-white/[0.06]">
                  <td className="px-3 py-2.5 text-white/85">{row.strategy}</td>
                  <td className="px-3 py-2.5">{row.quality}</td>
                  <td className="px-3 py-2.5">{row.vlmCallShare}</td>
                  <td className="px-3 py-2.5">
                    <ProvenanceBadge kind={row.provenance} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <Link
          href="/eval"
          className="mt-6 inline-flex font-mono text-[10px] uppercase tracking-[0.14em] text-white/45 transition-colors hover:text-white/75"
        >
          打开完整 Evaluation →
        </Link>
      </div>
    </section>
  );
}
