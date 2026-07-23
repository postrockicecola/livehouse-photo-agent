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
  hardware: "stamped in report.protocol.hardware when regenerated via scripts/eval/protocol.py",
  realRun: true,
  provenance: "recorded",
  reportPath: "reports/eval/stage3_v6_qwen2vl_temp0.json",
  metricNotes:
    "Quality = Spearman / MAE on overall vs human labels; Precision@K on human keepers. Eval config admits all frames to Stage3 (not production cull rates). New reports include protocol (seed/config/hardware/git).",
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
    quality: "admitted Spearman 0.52 · MAE 4.48 · keeper coverage 0.06",
    vlmCallShare: "16 / 250 = 6.4%",
    latency: "—",
    cost: "~15× fewer VLM calls vs full",
    provenance: "recorded",
    notes:
      "Offline replay of production apply_stage3_candidates_gating on the same 250 labels (not a second GPU pass). Gated Spearman is on the 16 admitted images only.",
    reportPath: "reports/eval/two_stage_gating.json",
  },
  {
    id: "agent-curate",
    strategy: "Agent 策展（budgeted planner）",
    quality: "sel. P 0.43 (stratified) · keeper recall 0.20",
    vlmCallShare: "40 / 250 = 16% (budget)",
    latency: "—",
    cost: "lower VLM count by design",
    provenance: "recorded",
    notes:
      "Default StratifiedHeuristicPlanner beats greedy heuristic (and random on P@10 / sel. P) under the same budget. LLM planner arm still ≈ heuristic with high fallback.",
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
    arm: "stratified",
    n: 250,
    budget: 40,
    selectionPrecision: 0.433,
    analyzedKeeperRecall: 0.205,
    precisionAt10: 0.5,
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
    "LLM planner ≈ greedy heuristic on this 60-image slice; llm_decision_rate ≈ 6% with frequent fallback. Stratified (default) already beats greedy on the full 250-set; LLM has not yet beaten stratified.",
};

export const QUANT_COMPARE_NOTE = {
  provenance: "simulated" as const,
  reportPath: "reports/eval/quant_compare_example.json",
  headline: "int4 vs fp16: ΔSpearman −0.009, est. $/1k −38%",
  note: "Example / illustrative quant_compare payload — mark Simulated. Do not cite as measured production SLO.",
};

/** Remaining gaps after the offline two-stage / stratified / preference scaffold. */
export const EVAL_GAPS = [
  {
    id: "preference_training_loop",
    title: "偏好数据 → SFT/DPO",
    detail:
      "data/eval/preferences/ 已导出 keep/reject pairs；训练与线上 reward 闭环尚未接入。",
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
    id: "two_stage",
    path: "reports/eval/two_stage_gating.json",
    provenance: "recorded" as const,
    summary: "Prod gating vs full-VLM · offline replay",
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
  { arm: "stratified", vlmSharePct: 16, precision: 0.433, label: "stratified @40" },
  { arm: "oracle", vlmSharePct: 16, precision: 0.933, label: "oracle @40" },
  { arm: "two-stage", vlmSharePct: 6.4, precision: 0.3125, label: "gated P@eff20" },
  { arm: "full-vlm", vlmSharePct: 100, precision: 0.55, label: "Stage3 P@20 (full)" },
] as const;

/* Resume bullets + interview pitches: docs/interview_pitch.txt — not on public pages. */
