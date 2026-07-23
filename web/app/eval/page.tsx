import type { Metadata } from "next";
import Link from "next/link";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import {
  AGENT_SELECTION_250,
  AGENT_SELECTION_LLM_60,
  EVAL_DATASET_META,
  EVAL_GAPS,
  EVAL_REPORT_INDEX,
  QUALITY_COST_POINTS,
  QUANT_COMPARE_NOTE,
  STAGE3_HEADLINE,
  STRATEGY_ROWS,
} from "@/lib/evalShowcase";

export const metadata: Metadata = {
  title: "Evaluation · Livehouse Photography Agent",
  description: "Recorded evaluation: Stage3 vs human labels, agent planner baselines, provenance-tagged metrics.",
};

function pct(n: number): string {
  return `${(n * 100).toFixed(0)}%`;
}

export default function EvalPage() {
  const maxPrec = Math.max(...QUALITY_COST_POINTS.map((p) => p.precision));

  return (
    <main className="min-h-screen bg-[#0a0a0a] px-5 py-10 text-white sm:px-8 lg:px-12">
      <div className="mx-auto max-w-5xl">
        <p className="font-mono text-[10px] uppercase tracking-[0.28em] text-white/32">Evaluation</p>
        <h1 className="mt-3 text-3xl font-light tracking-tight text-white/[0.92] sm:text-4xl">
          固定评估集上的策略对比
        </h1>
        <p className="mt-4 max-w-2xl text-sm leading-relaxed text-white/40">
          数字来自仓库内已提交报告。空单元格表示尚无真实测量，不填估算。每条结果带 provenance。
        </p>

        <section className="mt-10 rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-light text-white/88">数据集与元数据</h2>
            <ProvenanceBadge kind={EVAL_DATASET_META.provenance} />
          </div>
          <dl className="mt-4 grid gap-3 text-sm text-white/45 sm:grid-cols-2">
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/28">Dataset</dt>
              <dd className="mt-1 text-white/70">{EVAL_DATASET_META.dataset}</dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/28">N</dt>
              <dd className="mt-1 text-white/70">{EVAL_DATASET_META.n} labeled images</dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/28">Model / config</dt>
              <dd className="mt-1 text-white/70">
                {EVAL_DATASET_META.model}
                <br />
                {EVAL_DATASET_META.config}
              </dd>
            </div>
            <div>
              <dt className="font-mono text-[10px] uppercase tracking-[0.14em] text-white/28">Hardware</dt>
              <dd className="mt-1 text-white/70">{EVAL_DATASET_META.hardware}</dd>
            </div>
          </dl>
          <p className="mt-3 text-[11px] leading-relaxed text-white/30">{EVAL_DATASET_META.metricNotes}</p>
          <p className="mt-1 font-mono text-[10px] text-white/25">{EVAL_DATASET_META.reportPath}</p>
        </section>

        <section className="mt-8">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-light text-white/88">策略总表</h2>
            <ProvenanceBadge kind="recorded" />
          </div>
          <div className="mt-4 overflow-x-auto rounded-xl border border-white/[0.08]">
            <table className="w-full min-w-[640px] text-left text-sm">
              <thead className="border-b border-white/[0.08] bg-white/[0.03] font-mono text-[10px] uppercase tracking-[0.12em] text-white/35">
                <tr>
                  <th className="px-3 py-2.5">策略</th>
                  <th className="px-3 py-2.5">质量</th>
                  <th className="px-3 py-2.5">VLM 调用</th>
                  <th className="px-3 py-2.5">延迟</th>
                  <th className="px-3 py-2.5">成本</th>
                  <th className="px-3 py-2.5">标签</th>
                </tr>
              </thead>
              <tbody className="text-white/65">
                {STRATEGY_ROWS.map((row) => (
                  <tr key={row.id} className="border-b border-white/[0.06]">
                    <td className="px-3 py-3 align-top text-white/85">{row.strategy}</td>
                    <td className="px-3 py-3 align-top">{row.quality}</td>
                    <td className="px-3 py-3 align-top">{row.vlmCallShare}</td>
                    <td className="px-3 py-3 align-top">{row.latency}</td>
                    <td className="px-3 py-3 align-top">{row.cost}</td>
                    <td className="px-3 py-3 align-top">
                      <ProvenanceBadge kind={row.provenance} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul className="mt-3 space-y-1.5 text-[11px] text-white/30">
            {STRATEGY_ROWS.map((row) => (
              <li key={`${row.id}-note`}>
                <span className="text-white/45">{row.strategy}：</span> {row.notes}{" "}
                <span className="font-mono text-white/25">({row.reportPath})</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-10 grid gap-6 lg:grid-cols-2">
          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-light text-white/88">Stage3 vs human</h2>
              <ProvenanceBadge kind={STAGE3_HEADLINE.provenance} />
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-white/30">Spearman</dt>
                <dd className="mt-1 text-2xl font-light tabular-nums text-white/90">{STAGE3_HEADLINE.spearman}</dd>
              </div>
              <div>
                <dt className="text-white/30">MAE</dt>
                <dd className="mt-1 text-2xl font-light tabular-nums text-white/90">{STAGE3_HEADLINE.mae}</dd>
              </div>
              <div>
                <dt className="text-white/30">P@10</dt>
                <dd className="mt-1 text-2xl font-light tabular-nums text-white/90">{pct(STAGE3_HEADLINE.precisionAt10)}</dd>
              </div>
              <div>
                <dt className="text-white/30">P@20</dt>
                <dd className="mt-1 text-2xl font-light tabular-nums text-white/90">{pct(STAGE3_HEADLINE.precisionAt20)}</dd>
              </div>
            </dl>
            <p className="mt-3 font-mono text-[10px] text-white/25">{STAGE3_HEADLINE.reportPath}</p>
          </div>

          <div className="rounded-2xl border border-white/[0.08] bg-white/[0.02] p-5">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-light text-white/88">质量 · VLM 调用份额</h2>
              <ProvenanceBadge kind="recorded" />
            </div>
            <p className="mt-2 text-[11px] text-white/35">
              Agent 臂：固定 budget=40/250（16% VLM）。全量点用 Stage3 P@20 作对照，指标定义不同，只看形状。
            </p>
            <ul className="mt-5 space-y-3">
              {QUALITY_COST_POINTS.map((p) => (
                <li key={p.label}>
                  <div className="flex justify-between font-mono text-[10px] text-white/40">
                    <span>{p.label}</span>
                    <span>
                      VLM {p.vlmSharePct}% · P {pct(p.precision)}
                    </span>
                  </div>
                  <div className="mt-1 h-2 overflow-hidden rounded-full bg-white/[0.06]">
                    <div
                      className="h-full rounded-full bg-white/35"
                      style={{ width: `${(p.precision / maxPrec) * 100}%` }}
                    />
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section className="mt-10">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-light text-white/88">Agent vs 非 Agent 基线</h2>
            <ProvenanceBadge kind="recorded" />
          </div>
          <p className="mt-2 max-w-2xl text-sm text-white/40">
            250 图、budget=40、selection_size=30。LLM 臂见下方 60 图子集。
          </p>
          <div className="mt-4 overflow-x-auto rounded-xl border border-white/[0.08]">
            <table className="w-full min-w-[560px] text-left text-sm">
              <thead className="border-b border-white/[0.08] bg-white/[0.03] font-mono text-[10px] uppercase tracking-[0.12em] text-white/35">
                <tr>
                  <th className="px-3 py-2.5">Arm</th>
                  <th className="px-3 py-2.5">Sel. precision</th>
                  <th className="px-3 py-2.5">Keeper recall</th>
                  <th className="px-3 py-2.5">P@10</th>
                  <th className="px-3 py-2.5">VLM used</th>
                </tr>
              </thead>
              <tbody className="text-white/65">
                {AGENT_SELECTION_250.map((arm) => (
                  <tr key={arm.arm} className="border-b border-white/[0.06]">
                    <td className="px-3 py-2.5 font-mono text-white/85">{arm.arm}</td>
                    <td className="px-3 py-2.5 tabular-nums">{arm.selectionPrecision.toFixed(2)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{arm.analyzedKeeperRecall.toFixed(3)}</td>
                    <td className="px-3 py-2.5 tabular-nums">{arm.precisionAt10.toFixed(2)}</td>
                    <td className="px-3 py-2.5 tabular-nums">
                      {arm.vlmCallsUsed}/{arm.n}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-4 rounded-xl border border-amber-400/20 bg-amber-400/[0.05] p-4">
            <div className="flex flex-wrap items-center gap-2">
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-amber-200/70">LLM arm (n=60)</p>
              <ProvenanceBadge kind={AGENT_SELECTION_LLM_60.provenance} />
            </div>
            <p className="mt-2 text-sm leading-relaxed text-amber-100/70">{AGENT_SELECTION_LLM_60.honesty}</p>
            <p className="mt-2 font-mono text-[10px] text-white/25">
              heuristic P={AGENT_SELECTION_LLM_60.arms[0].selectionPrecision} · llm P=
              {AGENT_SELECTION_LLM_60.arms[1].selectionPrecision} · decision_rate=
              {AGENT_SELECTION_LLM_60.arms[1].llmDecisionRate} · {AGENT_SELECTION_LLM_60.reportPath}
            </p>
          </div>
        </section>

        <section className="mt-10 rounded-2xl border border-dashed border-white/15 p-5">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-light text-white/80">Quantization example</h2>
            <ProvenanceBadge kind={QUANT_COMPARE_NOTE.provenance} />
          </div>
          <p className="mt-2 text-sm text-white/45">{QUANT_COMPARE_NOTE.headline}</p>
          <p className="mt-2 text-[11px] text-white/30">{QUANT_COMPARE_NOTE.note}</p>
          <p className="mt-1 font-mono text-[10px] text-white/25">{QUANT_COMPARE_NOTE.reportPath}</p>
        </section>

        <section className="mt-10">
          <h2 className="text-lg font-light text-white/88">尚未填写的对照</h2>
          <ul className="mt-3 space-y-2 text-sm text-white/40">
            {EVAL_GAPS.map((g) => (
              <li key={g.id} className="rounded-xl border border-white/[0.06] px-4 py-3">
                <p className="text-white/70">{g.title}</p>
                <p className="mt-1 text-[12px] leading-relaxed text-white/35">{g.detail}</p>
              </li>
            ))}
          </ul>
        </section>

        <section className="mt-10">
          <h2 className="text-lg font-light text-white/88">报告索引</h2>
          <ul className="mt-3 space-y-2">
            {EVAL_REPORT_INDEX.map((r) => (
              <li
                key={r.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-white/[0.06] px-3 py-2 text-sm"
              >
                <div>
                  <p className="text-white/70">{r.summary}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-white/28">{r.path}</p>
                </div>
                <ProvenanceBadge kind={r.provenance} />
              </li>
            ))}
          </ul>
        </section>

        <div className="mt-12 flex flex-wrap gap-3">
          <Link
            href="/infra?tour=1"
            className="rounded-full bg-white px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-[#0a0a0a]"
          >
            Infra walkthrough
          </Link>
          <Link
            href="/"
            className="rounded-full border border-white/20 px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-white/70"
          >
            首页
          </Link>
          <Link
            href="/gallery"
            className="rounded-full border border-white/20 px-5 py-2.5 font-mono text-[10px] uppercase tracking-[0.14em] text-white/70"
          >
            Gallery
          </Link>
        </div>
      </div>
    </main>
  );
}
