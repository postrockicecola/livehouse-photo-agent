/**
 * Curated evaluation showcase for portfolio surfaces.
 * Numbers are copied from committed reports — blanks stay blank (no estimates).
 */
import type { ProvenanceKind } from "@/lib/provenance";

export type EvalMeta = {
  dataset: string;
  n: number;
  model?: string;
  config?: string;
  hardware?: string;
  realRun: boolean;
  provenance: ProvenanceKind;
  reportPath: string;
  metricNotes: string;
};

export type StrategyRow = {
  id: string;
  strategy: string;
  quality: string;
  vlmCallShare: string;
  latency: string;
  cost: string;
  provenance: ProvenanceKind;
  notes: string;
  reportPath: string;
};

export type AgentArmRow = {
  arm: string;
  n: number;
  budget: number;
  selectionPrecision: number;
  analyzedKeeperRecall: number;
  precisionAt10: number;
  vlmCallsUsed: number;
  provenance: ProvenanceKind;
  reportPath: string;
};

export const EVAL_DATASET_META: EvalMeta = {
  dataset: "data/eval — stratified Livehouse archive sample + human labels",
  n: 250,
  model: "qwen2-vl (temp0; see stage3_v6_qwen2vl_temp0 report)",
  config: "configs/eval_stage3.yaml (admission gates opened for full scoring)",
  hardware: "not recorded in report JSON",
  realRun: true,
  provenance: "recorded",
  reportPath: "reports/eval/stage3_v6_qwen2vl_temp0.json",
  metricNotes:
    "Quality = Spearman / MAE on overall vs human labels; Precision@K on human keepers. Eval config admits all frames to Stage3 (not production cull rates).",
};

/** Guide §4.4 strategy table — only fill cells backed by reports. */
export const STRATEGY_ROWS: StrategyRow[] = [
  {
    id: "full-vlm",
    strategy: "全量 VLM（eval Stage3）",
    quality: "Spearman 0.36 · MAE 6.09 · P@20 0.55",
    vlmCallShare: "100% of 250 eval images",
    latency: "—",
    cost: "—",
    provenance: "recorded",
    notes: "Baseline calibration on fixed labels. Latency/cost not in this report.",
    reportPath: "reports/eval/stage3_v6_qwen2vl_temp0.json",
  },
  {
    id: "two-stage",
    strategy: "两阶段门控（生产路径）",
    quality: "—",
    vlmCallShare: "—",
    latency: "—",
    cost: "—",
    provenance: "recorded",
    notes:
      "Production OpenCV → aesthetic → VLM path exists, but no committed head-to-head vs full-VLM on the same 250 labels yet. Left blank on purpose.",
    reportPath: "configs/livehouse.yaml · services/processor/",
  },
  {
    id: "agent-curate",
    strategy: "Agent 策展（budgeted planner）",
    quality: "P@30 0.30 (heuristic) · keeper recall 0.16",
    vlmCallShare: "40 / 250 = 16% (budget)",
    latency: "—",
    cost: "lower VLM count by design",
    provenance: "recorded",
    notes:
      "Quality has not stably beaten the heuristic baseline. LLM planner arm (60-img subset) ≈ heuristic with high fallback rate.",
    reportPath: "reports/eval/agent_selection.json",
  },
];

export const STAGE3_HEADLINE = {
  provenance: "recorded" as const,
  n: 250,
  spearman: 0.362,
  pearson: 0.843,
  mae: 6.09,
  precisionAt10: 0.4,
  precisionAt20: 0.55,
  humanKeepers: 83,
  reportPath: "reports/eval/stage3_v6_qwen2vl_temp0.json",
  model: "qwen2-vl temp0",
  config: "configs/eval_stage3.yaml",
};

/** 250-img planner comparison (no LLM arm in this report). */
export const AGENT_SELECTION_250: AgentArmRow[] = [
  {
    arm: "random",
    n: 250,
    budget: 40,
    selectionPrecision: 0.4,
    analyzedKeeperRecall: 0.205,
    precisionAt10: 0.4,
    vlmCallsUsed: 40,
    provenance: "recorded",
    reportPath: "reports/eval/agent_selection.json",
  },
  {
    arm: "heuristic",
    n: 250,
    budget: 40,
    selectionPrecision: 0.3,
    analyzedKeeperRecall: 0.157,
    precisionAt10: 0.4,
    vlmCallsUsed: 40,
    provenance: "recorded",
    reportPath: "reports/eval/agent_selection.json",
  },
  {
    arm: "oracle",
    n: 250,
    budget: 40,
    selectionPrecision: 0.933,
    analyzedKeeperRecall: 0.458,
    precisionAt10: 0.8,
    vlmCallsUsed: 40,
    provenance: "recorded",
    reportPath: "reports/eval/agent_selection.json",
  },
];

/** Smaller LLM-included run — show honesty, not a production claim. */
export const AGENT_SELECTION_LLM_60 = {
  provenance: "recorded" as const,
  n: 60,
  budget: 15,
  humanKeepers: 20,
  reportPath: "reports/eval/agent_selection_llm.json",
  arms: [
    { arm: "heuristic", selectionPrecision: 0.333, analyzedKeeperRecall: 0.06, llmDecisionRate: null as number | null },
    { arm: "llm", selectionPrecision: 0.357, analyzedKeeperRecall: 0.06, llmDecisionRate: 0.063 },
  ],
  honesty:
    "LLM planner ≈ heuristic on this 60-image slice; llm_decision_rate ≈ 6% with frequent heuristic fallback. Runtime (budget, tools, eval harness) is ready; quality upside is not yet proven.",
};

export const QUANT_COMPARE_NOTE = {
  provenance: "simulated" as const,
  reportPath: "reports/eval/quant_compare_example.json",
  headline: "int4 vs fp16: ΔSpearman −0.009, est. $/1k −38%",
  note: "Example / illustrative quant_compare payload — mark Simulated. Do not cite as measured production SLO.",
};

/** Known missing experiments — keep blank in tables until a real report lands. */
export const EVAL_GAPS = [
  {
    id: "two_stage_vs_full_vlm",
    title: "两阶段门控 vs 全量 VLM",
    detail:
      "生产路径（OpenCV → 美学分 → VLM）已实现，但仓库里还没有同一 250 标注集上的质量 / 调用比例 / 延迟 / 成本对照报告。",
  },
] as const;

export const EVAL_REPORT_INDEX = [
  {
    id: "stage3",
    path: "reports/eval/stage3_v6_qwen2vl_temp0.json",
    provenance: "recorded" as const,
    summary: "Stage3 vs human · n=250",
  },
  {
    id: "agent250",
    path: "reports/eval/agent_selection.json",
    provenance: "recorded" as const,
    summary: "Planner baselines · n=250 · budget=40",
  },
  {
    id: "agent60",
    path: "reports/eval/agent_selection_llm.json",
    provenance: "recorded" as const,
    summary: "LLM planner slice · n=60",
  },
  {
    id: "meta",
    path: "reports/eval/meta.json",
    provenance: "recorded" as const,
    summary: "Provenance index for all eval showcase reports",
  },
  {
    id: "quant",
    path: "reports/eval/quant_compare_example.json",
    provenance: "simulated" as const,
    summary: "Quantization example only",
  },
] as const;

/** Simple quality vs VLM-budget points for a chart (from agent_selection 250). */
export const QUALITY_COST_POINTS = [
  { arm: "heuristic", vlmSharePct: 16, precision: 0.3, label: "heuristic @40" },
  { arm: "random", vlmSharePct: 16, precision: 0.4, label: "random @40" },
  { arm: "oracle", vlmSharePct: 16, precision: 0.933, label: "oracle @40" },
  { arm: "full-vlm", vlmSharePct: 100, precision: 0.55, label: "Stage3 P@20 (full)" },
] as const;

/* Resume bullets + interview pitches: docs/interview_pitch.txt — not on public pages. */
