"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkline } from "@/components/infra/Sparkline";
import { ProvenanceBadge } from "@/components/ProvenanceBadge";
import { AppNav } from "@/components/ui/AppNav";
import { EmptyState, ErrorState, LoadingState, Panel } from "@/components/ui/states";
import type {
  AgentEvalCase,
  AgentEvalHistory,
  AgentEvalHistoryItem,
  AgentEvalReport,
} from "@/lib/agentEval";
import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();

function number(value: number | null | undefined, digits = 1): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}

function percent(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}

function duration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms.toFixed(0)}ms`;
}

function dateTime(value?: string): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusOf(report: AgentEvalReport): { label: string; detail: string; tone: string; dot: string } {
  const completed = report.run.completed_at ? new Date(report.run.completed_at).valueOf() : 0;
  const stale = !completed || Date.now() - completed > 36 * 60 * 60 * 1000;
  if (!report.passed) {
    return {
      label: "RED",
      detail: "质量门槛或 baseline 回归未通过",
      tone: "border-rose-400/25 bg-rose-400/[0.08] text-rose-100",
      dot: "bg-rose-400",
    };
  }
  if (stale) {
    return {
      label: "STALE",
      detail: "最近一次报告已超过 36 小时",
      tone: "border-amber-400/25 bg-amber-400/[0.08] text-amber-100",
      dot: "bg-amber-400",
    };
  }
  return {
    label: "GREEN",
    detail: "行为门槛和 baseline 回归均通过",
    tone: "border-emerald-400/25 bg-emerald-400/[0.08] text-emerald-100",
    dot: "bg-emerald-400",
  };
}

function MetricCard({
  label,
  value,
  detail,
  trend,
}: {
  label: string;
  value: string;
  detail: string;
  trend?: Array<number | null>;
}) {
  return (
    <Panel className="min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/30">{label}</p>
          <p className="mt-2 text-3xl font-light tabular-nums text-white/90">{value}</p>
          <p className="mt-1 truncate text-[11px] text-white/32">{detail}</p>
        </div>
        {trend ? <Sparkline data={trend} width={82} height={34} className="mt-1 shrink-0 text-sky-300/75" /> : null}
      </div>
    </Panel>
  );
}

function TrendChart({ runs }: { runs: AgentEvalHistoryItem[] }) {
  const ordered = [...runs].reverse().filter((run) => run.overall_score != null);
  if (ordered.length < 2) {
    return <EmptyState title="趋势将在第二次 Daily 评测后出现" description="当前仍可查看最新运行的完整指标。" />;
  }
  const width = 760;
  const height = 220;
  const pad = { left: 38, right: 16, top: 18, bottom: 28 };
  const x = (index: number) =>
    pad.left + (index / Math.max(1, ordered.length - 1)) * (width - pad.left - pad.right);
  const y = (value: number) => pad.top + ((100 - value) / 100) * (height - pad.top - pad.bottom);
  const line = ordered
    .map((run, index) => `${index === 0 ? "M" : "L"}${x(index).toFixed(1)},${y(Number(run.overall_score)).toFixed(1)}`)
    .join(" ");

  return (
    <div className="mt-4 overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${height}`} className="h-[220px] min-w-[620px] w-full" role="img" aria-label="Daily overall score trend">
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} stroke="white" strokeOpacity="0.07" />
            <text x={pad.left - 8} y={y(tick) + 3} textAnchor="end" fill="white" fillOpacity="0.28" fontSize="9">
              {tick}
            </text>
          </g>
        ))}
        <line x1={pad.left} x2={width - pad.right} y1={y(80)} y2={y(80)} stroke="#fbbf24" strokeOpacity="0.45" strokeDasharray="4 5" />
        <path d={line} fill="none" stroke="#7dd3fc" strokeWidth="2" strokeLinejoin="round" />
        {ordered.map((run, index) => (
          <g key={run.run_id}>
            <circle cx={x(index)} cy={y(Number(run.overall_score))} r="4" fill={run.passed ? "#6ee7b7" : "#fb7185"}>
              <title>{`${dateTime(run.started_at)} · ${number(run.overall_score)} · ${run.passed ? "PASS" : "FAIL"}`}</title>
            </circle>
            {(index === 0 || index === ordered.length - 1) && (
              <text x={x(index)} y={height - 8} textAnchor={index === 0 ? "start" : "end"} fill="white" fillOpacity="0.28" fontSize="9">
                {dateTime(run.started_at).slice(0, 5)}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

function ScoreBars({ report }: { report: AgentEvalReport }) {
  const rows = [
    ["Task success", report.score.task_success],
    ["Photography quality", report.score.quality],
    ["Trajectory", report.score.trajectory],
    ["Cost efficiency", report.score.cost],
  ] as const;
  return (
    <div className="mt-5 space-y-4">
      {rows.map(([label, value]) => (
        <div key={label}>
          <div className="flex justify-between font-mono text-[10px] text-white/38">
            <span>{label}</span>
            <span>{number(value)}</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
            <div className="h-full rounded-full bg-sky-300/55" style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function CaseRow({ item }: { item: AgentEvalCase }) {
  return (
    <details className="group border-b border-white/[0.06] last:border-b-0">
      <summary className="grid cursor-pointer list-none gap-3 px-4 py-4 transition-colors hover:bg-white/[0.02] sm:grid-cols-[minmax(0,1.6fr)_110px_90px_90px_24px] sm:items-center">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className={`h-1.5 w-1.5 rounded-full ${item.passed ? "bg-emerald-400" : "bg-rose-400"}`} />
            <span className="truncate text-[13px] text-white/78">{item.id}</span>
          </div>
          <p className="mt-1 truncate pl-3.5 text-[11px] text-white/30">{item.description}</p>
        </div>
        <span className="font-mono text-[10px] text-white/45">{item.actual_intent ?? "—"}</span>
        <span className="font-mono text-[10px] text-white/45">{duration(item.latency_ms)}</span>
        <span className="font-mono text-[10px] text-white/45">{item.steps} steps</span>
        <span className="hidden text-white/25 transition-transform group-open:rotate-45 sm:block">+</span>
      </summary>
      <div className="grid gap-4 bg-white/[0.015] px-4 pb-5 pt-1 text-[11px] sm:grid-cols-2 lg:grid-cols-4">
        <div className="sm:col-span-2">
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Prompt</p>
          <p className="mt-1.5 text-white/55">{item.user_input || "—"}</p>
        </div>
        <div>
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Tools</p>
          <p className="mt-1.5 text-white/55">{item.tools.join(" → ") || "No tool calls"}</p>
        </div>
        <div>
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Budget</p>
          <p className="mt-1.5 text-white/55">{item.llm_calls} LLM · {item.tool_calls} tools · {item.tokens} tokens</p>
        </div>
        <div>
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Intent</p>
          <p className="mt-1.5 text-white/55">{item.expected_intent ?? "—"} → {item.actual_intent ?? "—"}</p>
        </div>
        <div>
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Trajectory / quality</p>
          <p className="mt-1.5 text-white/55">{number(item.trajectory_score)} / {number(item.quality_score)}</p>
        </div>
        <div className="sm:col-span-2">
          <p className="font-mono uppercase tracking-[0.12em] text-white/25">Checks</p>
          <p className={`mt-1.5 ${item.failed_checks.length ? "text-rose-200/75" : "text-emerald-200/65"}`}>
            {item.failed_checks.length ? `Failed: ${item.failed_checks.join(", ")}` : "All behavior checks passed"}
          </p>
          {item.failure?.reason ? <p className="mt-1 text-rose-200/55">{item.failure.reason}</p> : null}
        </div>
      </div>
    </details>
  );
}

export default function AgentEvalDashboard() {
  const [report, setReport] = useState<AgentEvalReport | null>(null);
  const [history, setHistory] = useState<AgentEvalHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [latestResponse, historyResponse] = await Promise.all([
        fetch(`${API_BASE}/api/eval/agent/latest?include_cases=true`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/eval/agent/runs?limit=30`, { cache: "no-store" }),
      ]);
      if (!latestResponse.ok) throw new Error(`latest report: HTTP ${latestResponse.status}`);
      if (!historyResponse.ok) throw new Error(`run history: HTTP ${historyResponse.status}`);
      setReport((await latestResponse.json()) as AgentEvalReport);
      setHistory((await historyResponse.json()) as AgentEvalHistory);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取 Agent 评测报告");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const orderedHistory = useMemo(() => [...(history?.runs ?? [])].reverse(), [history]);
  const status = report ? statusOf(report) : null;
  const behavior = report?.metrics.behavior;
  const quality = report?.metrics.quality;
  const runtime = report?.metrics.runtime;
  const trajectory = report?.metrics.trajectory;

  return (
    <div className="min-h-screen bg-[#0a0a0a] text-white">
      <AppNav />
      <main className="px-5 py-9 sm:px-8 lg:px-12">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-wrap items-end justify-between gap-5">
            <div>
              <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.2em] text-white/30">
                <Link href="/eval" className="transition-colors hover:text-white/55">Evaluation</Link>
                <span>/</span>
                <span>Agent Daily</span>
              </div>
              <h1 className="mt-3 text-3xl font-light tracking-tight text-white/90 sm:text-4xl">Agent 评测控制台</h1>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/38">
                生产 LangGraph 路径的每日行为契约、摄影质量、运行成本与 baseline 回归。
              </p>
            </div>
            {report && status ? (
              <div className={`rounded-xl border px-4 py-3 ${status.tone}`}>
                <div className="flex items-center gap-2 font-mono text-[10px] tracking-[0.16em]">
                  <span className={`h-2 w-2 rounded-full ${status.dot}`} />
                  {status.label}
                  <ProvenanceBadge kind="recorded" className="ml-1" />
                </div>
                <p className="mt-1 text-[10px] opacity-55">{status.detail}</p>
              </div>
            ) : null}
          </div>

          {loading ? <LoadingState label="读取 Daily Agent Eval…" className="mt-8" /> : null}
          {error ? <ErrorState message={error} onRetry={() => void load()} className="mt-8" /> : null}

          {report ? (
            <>
              <div className="mt-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard
                  label="Overall score"
                  value={number(report.score.overall_score)}
                  detail={`门槛 ${number(report.thresholds?.min_overall_score, 0)} · ${report.passed ? "PASS" : "FAIL"}`}
                  trend={orderedHistory.map((run) => run.overall_score ?? null)}
                />
                <MetricCard
                  label="Behavior success"
                  value={percent(behavior?.success_rate)}
                  detail={`${behavior?.passed ?? 0}/${behavior?.total ?? 0} contracts · intent ${percent(behavior?.intent_accuracy)}`}
                  trend={orderedHistory.map((run) => run.behavior_success_rate ?? null)}
                />
                <MetricCard
                  label="Photography quality"
                  value={number(quality?.quality_score)}
                  detail={`P@K ${percent(quality?.precision_at_k)} · NDCG ${number(quality?.ndcg, 2)}`}
                  trend={orderedHistory.map((run) => run.quality_score ?? null)}
                />
                <MetricCard
                  label="P95 latency"
                  value={duration(runtime?.p95_latency_ms)}
                  detail={`P50 ${duration(runtime?.p50_latency_ms)} · total ${duration(runtime?.total_latency_ms)}`}
                  trend={orderedHistory.map((run) => run.p95_latency_ms ?? null)}
                />
              </div>

              <div className="mt-6 grid gap-6 xl:grid-cols-[minmax(0,1.65fr)_minmax(300px,0.75fr)]">
                <Panel>
                  <div className="flex items-center justify-between gap-3">
                    <div>
                      <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">30-day signal</p>
                      <h2 className="mt-1 text-lg font-light text-white/78">Overall score 趋势</h2>
                    </div>
                    <span className="font-mono text-[9px] text-white/25">threshold 80</span>
                  </div>
                  <TrendChart runs={history?.runs ?? []} />
                </Panel>

                <Panel>
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Weighted score</p>
                  <h2 className="mt-1 text-lg font-light text-white/78">评分构成</h2>
                  <ScoreBars report={report} />
                </Panel>
              </div>

              <div className="mt-6 grid gap-6 lg:grid-cols-3">
                <Panel>
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Trajectory</p>
                  <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                    <div><dt className="text-white/30">Score</dt><dd className="mt-1 text-xl text-white/75">{number(trajectory?.trajectory_score)}</dd></div>
                    <div><dt className="text-white/30">Avg steps</dt><dd className="mt-1 text-xl text-white/75">{number(trajectory?.average_steps)}</dd></div>
                    <div><dt className="text-white/30">Tool calls</dt><dd className="mt-1 text-xl text-white/75">{number(trajectory?.average_tool_calls)}</dd></div>
                    <div><dt className="text-white/30">Loops / retries</dt><dd className="mt-1 text-xl text-white/75">{trajectory?.loop_count ?? 0} / {trajectory?.retry_count ?? 0}</dd></div>
                  </dl>
                </Panel>

                <Panel>
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Runtime & cost</p>
                  <dl className="mt-4 grid grid-cols-2 gap-4 text-sm">
                    <div><dt className="text-white/30">Avg tokens</dt><dd className="mt-1 text-xl text-white/75">{number(runtime?.average_tokens, 0)}</dd></div>
                    <div><dt className="text-white/30">Avg LLM calls</dt><dd className="mt-1 text-xl text-white/75">{number(runtime?.average_llm_calls)}</dd></div>
                    <div><dt className="text-white/30">Cost score</dt><dd className="mt-1 text-xl text-white/75">{number(runtime?.cost_score)}</dd></div>
                    <div><dt className="text-white/30">Token source</dt><dd className="mt-1 font-mono text-[11px] text-white/55">{runtime?.token_source ?? "—"}</dd></div>
                  </dl>
                </Panel>

                <Panel className={report.regression?.passed === false ? "border-rose-400/25" : ""}>
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Baseline regression</p>
                  <div className="mt-4 flex items-center gap-2">
                    <span className={`h-2 w-2 rounded-full ${report.regression?.passed === false ? "bg-rose-400" : "bg-emerald-400"}`} />
                    <span className="text-xl font-light text-white/75">
                      {report.regression?.available === false ? "No baseline" : report.regression?.passed === false ? "Regression" : "Stable"}
                    </span>
                  </div>
                  <p className="mt-3 text-[11px] leading-relaxed text-white/35">{report.regression?.summary ?? "—"}</p>
                  <p className="mt-3 font-mono text-[9px] text-white/25">
                    quality Δ {number(report.regression?.deltas?.quality, 3)} · latency Δ {number(report.regression?.deltas?.p95_latency_ms, 3)}
                  </p>
                </Panel>
              </div>

              {(report.threshold_failures.length > 0 || (report.regression?.regressions?.length ?? 0) > 0) ? (
                <Panel className="mt-6 border-rose-400/25 bg-rose-400/[0.04]">
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-rose-200/55">Blocking failures</p>
                  <ul className="mt-3 space-y-1 text-[11px] text-rose-100/70">
                    {[...report.threshold_failures, ...(report.regression?.regressions ?? [])].map((failure, index) => (
                      <li key={`${String(failure)}-${index}`}>• {String(failure)}</li>
                    ))}
                  </ul>
                </Panel>
              ) : null}

              <section className="mt-8">
                <div className="flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Behavior contracts</p>
                    <h2 className="mt-1 text-xl font-light text-white/82">逐 Case 下钻</h2>
                  </div>
                  <span className="font-mono text-[9px] text-white/25">{report.cases?.length ?? 0} cases · 点击展开</span>
                </div>
                <div className="mt-4 overflow-hidden rounded-xl border border-white/[0.08] bg-white/[0.015]">
                  {report.cases?.length ? report.cases.map((item) => <CaseRow key={item.id} item={item} />) : (
                    <EmptyState title="本次报告没有 case 明细" />
                  )}
                </div>
              </section>

              <section className="mt-8">
                <div>
                  <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-white/28">Run ledger</p>
                  <h2 className="mt-1 text-xl font-light text-white/82">最近运行</h2>
                </div>
                <div className="mt-4 overflow-x-auto rounded-xl border border-white/[0.08]">
                  <table className="w-full min-w-[760px] text-left text-[11px]">
                    <thead className="border-b border-white/[0.08] bg-white/[0.025] font-mono uppercase tracking-[0.1em] text-white/28">
                      <tr><th className="px-4 py-3">Run</th><th className="px-3 py-3">Status</th><th className="px-3 py-3">Score</th><th className="px-3 py-3">Behavior</th><th className="px-3 py-3">Quality</th><th className="px-3 py-3">P95</th><th className="px-3 py-3">Model</th></tr>
                    </thead>
                    <tbody>
                      {(history?.runs ?? []).slice(0, 12).map((run) => (
                        <tr key={run.run_id} className="border-b border-white/[0.05] text-white/48 last:border-b-0">
                          <td className="px-4 py-3"><p className="text-white/65">{dateTime(run.started_at)}</p><p className="mt-0.5 max-w-[180px] truncate font-mono text-[9px] text-white/22">{run.run_id}</p></td>
                          <td className="px-3 py-3"><span className={run.passed ? "text-emerald-300/75" : "text-rose-300/80"}>{run.passed ? "PASS" : "FAIL"}</span></td>
                          <td className="px-3 py-3 tabular-nums">{number(run.overall_score)}</td>
                          <td className="px-3 py-3 tabular-nums">{percent(run.behavior_success_rate)}</td>
                          <td className="px-3 py-3 tabular-nums">{number(run.quality_score)}</td>
                          <td className="px-3 py-3 tabular-nums">{duration(run.p95_latency_ms)}</td>
                          <td className="px-3 py-3 font-mono text-[9px]">{run.model?.model_name ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <footer className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.07] py-5 font-mono text-[9px] text-white/25">
                <span>{report.run.run_id} · {report.run.model?.provider}/{report.run.model?.model_name}</span>
                <span>完成于 {dateTime(report.run.completed_at)} · {duration(report.run.duration_ms)} · {report.run.case_count ?? 0} cases</span>
              </footer>
            </>
          ) : null}
        </div>
      </main>
    </div>
  );
}
