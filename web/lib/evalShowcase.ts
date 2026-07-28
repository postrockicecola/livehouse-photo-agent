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


export const QUANT_COMPARE_NOTE = {
  provenance: "simulated" as const,
  reportPath: "reports/eval/quant_compare_example.json",
  headline: "int4 vs fp16: ΔSpearman −0.009, est. $/1k −38%",
  note: "Example / illustrative quant_compare payload — mark Simulated. Do not cite as measured production SLO.",
};

/** Remaining gaps after the offline two-stage / preference scaffold. */
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

/** Simple quality vs VLM-budget points for a chart (gating vs full Stage3). */
export const QUALITY_COST_POINTS = [
  { arm: "two-stage", vlmSharePct: 6.4, precision: 0.3125, label: "gated P@eff20" },
  { arm: "full-vlm", vlmSharePct: 100, precision: 0.55, label: "Stage3 P@20 (full)" },
] as const;

/* Numbers below are sourced from reports/eval/* — do not invent latency/cost. */
