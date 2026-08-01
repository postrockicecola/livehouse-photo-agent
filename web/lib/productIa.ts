/**
 * Luma product information architecture
 *
 * Portfolio narrative (Batch A): product value first, then job-centric AI runtime.
 * Main path = ingest → cheap gates → durable jobs → bounded VLM → ledger → Gallery/Infra.
 * Gallery chat Agent (LangGraph decide→act→answer) sits on that path.
 */

export const MARKETING_HOME = "/";
export const STUDIO_HOME = "/studio";

/** Shared one-liner — keep README / landing aligned. */
export const PROJECT_POSITIONING = {
  oneLinerZh:
    "面向视觉工作流的 job-centric AI runtime：用持久作业状态机管理 VLM 推理，通过背压、降级、运行账本和控制台保证任务可恢复、可观察、可评估。",
  oneLinerEn:
    "A job-centric AI runtime for vision workflows: durable job state machines for VLM inference, with backpressure, fallback, a run ledger, and an operator console so work is recoverable, observable, and evaluable.",
  mainPathZh: "照片导入 → 低成本视觉门控 → 持久作业系统 → 有界 VLM 推理 → 模型与运行账本 → Gallery / Infra Console",
  sells: [
    { id: "durable", label: "Durable Jobs", caption: "任务状态可恢复" },
    { id: "bounded", label: "Bounded Inference", caption: "并发与背压有边界" },
    { id: "fallback", label: "Model Fallback", caption: "主模型异常可降级" },
    { id: "obs", label: "End-to-end Observability", caption: "作业与模型调用可追踪" },
  ],
  boundaries: [
    "SQLite 作为单节点执行事实源（非集群分布式数据库）",
    "推理准入与背压以进程内队列为主（非集群级配额）",
    "产物依赖单节点共享卷 / 本地 archive 路径",
  ],
} as const;

/**
 * Primary marketing CTA — name the Agent, land on Gallery (chat surface).
 * Studio remains the session / ANALYZE workbench via secondary CTAs.
 */
export const LANDING_AGENT_HOME = "/gallery";
export const LANDING_STUDIO_CTA = "打开 Agent";

/**
 * Showcase Studio Agent — three ladder steps:
 * 1. select top-10 → 2. style chips → 3. find guitarist (verified only)
 */
export const STUDIO_SELECT_PROMPTS = ["帮我选出得分最高的 10 张"] as const;

/** Phase 2: after first select — multiple film / dreamcore grades. */
export const STUDIO_STYLE_PROMPTS = [
  "试试修成 Cinestill 800T 风格",
  "试试修成 Kodak Portra 暖调风格",
  "试试修成富士 Superia 青绿风格",
  "试试修成梦核式修图风格",
  "试试修成 HP5 银盐黑白风格",
] as const;

/** Phase 3: after a style has been applied — single verified subject search. */
export const STUDIO_FIND_PROMPTS = ["找出吉他手弹琴的特写"] as const;

/** Landing hero rotator — broader demo copy (not the Studio three-step ladder). */
export const LANDING_HERO_PROMPTS = [
  "帮我选出得分最高的 10 张",
  "试试修成 Cinestill 800T 风格",
  "找出吉他手弹琴的特写",
  "把糊的、过曝的先剔掉",
  "连拍里每组只留一张最好的",
  "选中的导出预览，RAW 也一起打包",
] as const;

export const LANDING_HERO = {
  eyebrow: "个人项目 · Job-centric AI Runtime",
  /** First-screen slogan — product value before infra nouns. */
  title: "现场照片，变成可交付的选片结果。",
  subtitle:
    "低成本门控与持久作业把场次算完；Gallery 对话 Agent 用自然语言搜、选、定风格、导出。摄影是真实负载，用来验证可恢复、可观察的推理作业系统。",
  description: PROJECT_POSITIONING.oneLinerZh,
  /**
   * Fixed first-viewport background (from ``data/eval/images/20260424__DSC04199.jpg``).
   * Bundled EXIF-stripped copy under ``web/public/showcase/`` so Vercel showcase stays stable.
   */
  backgroundSrc: "/showcase/landing-hero.jpg",
  ctaPrimary: LANDING_STUDIO_CTA,
  ctaSecondary: { label: "看主链路", href: "#workflow" },
  promptIdle: "试试：找出吉他手特写…",
  /** Hero prompt submits into Gallery chat (Agent opens with ``?q=``). */
  promptSubmitHref: LANDING_AGENT_HOME,
  /** Hero CTAs — Agent first; Studio / Infra as supporting surfaces. */
  promptCtas: [
    { label: "打开 Agent", href: LANDING_AGENT_HOME, primary: true },
    { label: "Studio 工作台", href: STUDIO_HOME },
    { label: "Infra 控制台", href: "/infra" },
  ],
} as const;

export type NavLink = {
  label: string;
  href: string;
  description?: string;
};

/** Landing top nav — keep short; deep pages live in footer / CTA. */
export const LANDING_NAV: NavLink[] = [
  { label: "结果", href: "#outcome", description: "一次运行的交付指标" },
  { label: "画廊", href: "#gallery", description: "筛选与确认" },
  { label: "主链路", href: "#workflow", description: "门控 → 作业 → VLM" },
];

export type WorkflowStep = {
  id: string;
  title: string;
  tagline: string;
};

export const LANDING_WORKFLOW = {
  eyebrow: "主链路",
  title: "从入库到可追踪的推理作业。",
  subtitle:
    "场次先建成可恢复的作业，再经 OpenCV → 美学分 → 有界 VLM；状态、调用与产物都写进账本。交付侧用 Gallery 确认，并用对话 Agent 操作选片结果。",
  phases: [
    { id: "ingest", label: "Ingest", range: [0, 0] },
    { id: "orchestrate", label: "Run", range: [1, 4] },
    { id: "deliver", label: "Deliver", range: [5, 6] },
  ],
  steps: [
    { id: "ingest", title: "Ingest", tagline: "导入现场预览与 RAW 索引。" },
    { id: "seed-jobs", title: "Create Jobs", tagline: "按场次写入可恢复作业。" },
    { id: "run-job", title: "Claim & Run", tagline: "Worker 原子认领后执行。" },
    { id: "pipeline-runner", title: "Cheap Gates", tagline: "OpenCV / 快速美学分先过滤。" },
    { id: "inference", title: "Bounded VLM", tagline: "有界并发、可降级的多模态推理。" },
    { id: "artifacts", title: "Ledger", tagline: "job / event / model_run / artifact 可追。" },
    { id: "gallery", title: "Gallery + Agent", tagline: "确认选片；对话搜、选、定风格、导出。" },
  ] satisfies WorkflowStep[],
} as const;

export type LandingBrainCounts = {
  jobs: number;
  events: number;
  artifacts: number;
  sessions: number;
  photos: number;
  snapshots: number;
};

export const LANDING_BRAIN_FALLBACK_COUNTS: LandingBrainCounts = {
  jobs: 0,
  events: 0,
  artifacts: 0,
  sessions: 50,
  photos: 573,
  snapshots: 0,
};

export type LandingInfraMetrics = {
  queue_depth: number;
  workers_online: number;
  workers_total: number;
  retry_pending: number;
  recovery_requeues: number;
  pipeline_active: number;
  monitoring_snapshots: number;
  dead_letter: number;
  /** Cumulative ledger totals — preferred for idle showcase snapshots. */
  jobs_total?: number;
  model_runs_total?: number;
};

export const LANDING_INFRA_FALLBACK_METRICS: LandingInfraMetrics = {
  queue_depth: 0,
  workers_online: 0,
  workers_total: 0,
  retry_pending: 0,
  recovery_requeues: 0,
  pipeline_active: 0,
  monitoring_snapshots: 0,
  dead_letter: 0,
  jobs_total: 0,
  model_runs_total: 0,
};

export type AiFlowStep = {
  id: string;
  label: string;
  tagline: string;
};

export type AiPipelineStage = {
  stage: string;
  title: string;
  name: string;
  body: string;
};

export const LANDING_AI_LAYER = {
  id: "ai-layer",
  eyebrow: "多阶段推理",
  title: "OpenCV → 美学分 → VLM。",
  subtitle: "固定 Stage 管道：前面便宜过滤，后面多模态分析，输出 caption、tags、分数。",
  flow: [
    { id: "image", label: "Image", tagline: "预览图输入" },
    { id: "vlm", label: "VLM", tagline: "有界并发推理" },
    { id: "caption", label: "Caption", tagline: "一句话描述" },
    { id: "tags", label: "Tags", tagline: "可检索标签" },
    { id: "score", label: "Score", tagline: "多维评分" },
  ] satisfies AiFlowStep[],
  stages: [
    {
      stage: "01",
      title: "Stage 1",
      name: "OpenCV",
      body: "信号和构图预筛，成本低。",
    },
    {
      stage: "02",
      title: "Stage 2",
      name: "Rule / Aesthetic",
      body: "规则和快速美学分，继续收窄。",
    },
    {
      stage: "03",
      title: "Stage 3",
      name: "VLM",
      body: "多模态分析，写出 caption / tags / score。",
    },
  ] satisfies AiPipelineStage[],
  preview: {
    /** Must match ``LANDING_HERO.backgroundSrc`` (/showcase/landing-hero.jpg). */
    caption: "鼓手近景，鼓棒带拖影，侧后紫光勾出轮廓。",
    tags: ["drummer", "motion blur", "peak energy"],
    score: "8.7",
    dimensions: "E 9.1 · T 8.4 · C 8.6",
  },
} as const;

export const LANDING_FOOTER_COLUMNS: { title: string; links: NavLink[] }[] = [
  {
    title: "界面",
    links: [
      { label: "Studio", href: "/studio" },
      { label: "Gallery", href: "/gallery" },
      { label: "Settings", href: "/config" },
    ],
  },
  {
    title: "内容",
    links: [
      { label: "结果", href: "#outcome" },
      { label: "主链路", href: "#workflow" },
      { label: "Gallery Agent", href: "/gallery" },
      { label: "Evaluation", href: "/eval" },
    ],
  },
  {
    title: "资源",
    links: [
      {
        label: "GitHub",
        href: "https://github.com/postrockicecola/livehouse-photo-agent",
      },
      { label: "README", href: "https://github.com/postrockicecola/livehouse-photo-agent#readme" },
    ],
  },
  {
    title: "运维",
    links: [
      { label: "Infra 控制台", href: "/infra" },
      { label: "五分钟 walkthrough", href: "/infra?tour=1" },
      { label: "Brain", href: "/infra/brain" },
    ],
  },
];

/** App shell — primary work routes (Studio / Gallery / Infra). */
export const APP_PRIMARY_NAV: NavLink[] = [
  { label: "Studio", href: "/studio" },
  { label: "Gallery", href: "/gallery" },
  { label: "Infra", href: "/infra" },
];

/** App shell — secondary routes. */
export const APP_MORE_NAV: NavLink[] = [
  { label: "Eval", href: "/eval" },
  { label: "Settings", href: "/config" },
  { label: "Site", href: "/" },
];
