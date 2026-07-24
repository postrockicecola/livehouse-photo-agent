/**
 * Batch B showcase walkthrough — representative jobs + recorded product metrics.
 * Used by Infra Guided Tour and the landing outcome strip.
 */

export const SHOWCASE_SUCCESS_JOB_ID = 61;
export const SHOWCASE_FALLBACK_JOB_ID = 62;

/** Recorded / Showcase Fixture numbers — not live SLOs. */
export const RECORDED_OUTCOME = {
  provenance: "recorded" as const,
  label: "Recorded session run",
  /** Representative ANALYZE_SESSION (#61) wall time. */
  jobId: SHOWCASE_SUCCESS_JOB_ID,
  photosIn: 412,
  vlmCalls: 288,
  keepRatePct: 79,
  /** Job #61 total_latency_ms ≈ 749984 → ~12.5 min */
  e2eMinutes: 12.5,
  notes:
    "VLM calls and keep rate from committed showcase fixtures (model_runs ledger + landing-stats). E2E is job #61 wall time. Session photo count is a representative showcase figure for that ANALYZE_SESSION shape — not a live counter.",
  sourceFiles: [
    "web/fixtures/infra-job-detail.json",
    "web/fixtures/infra-metrics.json",
    "web/fixtures/landing-stats.json",
  ],
} as const;

export const WALKTHROUGH_CASES = [
  {
    id: "success",
    jobId: SHOWCASE_SUCCESS_JOB_ID,
    title: "成功作业",
    summary: "QUEUED → CLAIMED → PREPROCESSING → INFERENCING → SUCCEEDED",
    detail: "点击打开事件时间线、产物与模型调用。",
  },
  {
    id: "fallback",
    jobId: SHOWCASE_FALLBACK_JOB_ID,
    title: "降级恢复",
    summary: "主模型超时 → fallback 接管 → degraded 成功",
    detail: "点击打开：primary TIMEOUT → fallback SUCCEEDED。",
  },
] as const;

export type TourStepId =
  | "jobs"
  | "success-job"
  | "model-calls"
  | "fallback-job"
  | "gallery"
  | "signals";

export type TourStep = {
  id: TourStepId;
  title: string;
  body: string;
  /** Element id to scroll/highlight on /infra */
  targetId: string;
  href?: string;
};

export const INFRA_TOUR_STEPS: TourStep[] = [
  {
    id: "jobs",
    title: "作业列表",
    body: "从 Job Explorer 看待处理与已完成作业。Showcase 默认提供两条代表性记录。",
    targetId: "tour-jobs",
  },
  {
    id: "success-job",
    title: "成功时间线",
    body: "展开 job #61：QUEUED → CLAIMED → PREPROCESSING → INFERENCING → SUCCEEDED。",
    targetId: "tour-jobs",
  },
  {
    id: "model-calls",
    title: "模型调用",
    body: "在作业详情里看 Provider calls：延迟、状态与产物归因。",
    targetId: "tour-jobs",
  },
  {
    id: "fallback-job",
    title: "失败与降级",
    body: "展开 job #62：主模型超时后 fallback 接管，作业以 degraded 成功收尾。",
    targetId: "tour-jobs",
  },
  {
    id: "gallery",
    title: "交付画廊",
    body: "到 Gallery 查看筛选结果与导出。",
    targetId: "tour-gallery-cta",
    href: "/gallery",
  },
  {
    id: "signals",
    title: "吞吐与成本",
    body: "回到 Golden Signals 与 Cost 面板，看队列、P95 与令牌归因。",
    targetId: "tour-signals",
  },
];
