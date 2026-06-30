/**
 * Luma product information architecture
 *
 * Story First · Product Second
 * ─────────────────────────────
 * /           Marketing — narrative, proof, visual gallery. No tools.
 * /studio     Workbench — sessions, pipeline, enter gallery.
 * /gallery    Deep work — curation & export (studio child).
 * /config     Studio settings (studio child).
 * /infra      Operator console — not linked from marketing nav.
 * /personal   Separate product line — footer / secondary entry only.
 */

export const MARKETING_HOME = "/";
export const STUDIO_HOME = "/studio";

/** Primary marketing CTA — nav bar + hero + closing section. */
export const LANDING_STUDIO_CTA = "Open Studio";

/** Hero — 3-second comprehension: Visual AI Workflow Platform. */
export const LANDING_HERO = {
  eyebrow: "Visual AI Workflow Platform",
  title: "看得见的工作流。",
  subtitle: "把一整场 Live 的上万张照片，自动筛成一份可交付的选片。",
  description:
    "OpenCV 多阶段预筛 → 美学评分 → 视觉语言模型分析，结果落成结构化档案。从 SD 卡入库到 Gallery 选片导出，整条链路可视、可追溯。",
  ctaPrimary: LANDING_STUDIO_CTA,
  ctaSecondary: { label: "See workflow", href: "#workflow" },
} as const;

/** Keys on the live `/api/landing/stats` payload a scale stat can bind to. */
export type LandingStatKey =
  | "sessions_total"
  | "photos_total"
  | "analyzed_photos_total"
  | "exported_photos_total";

export type LandingScaleStat = {
  id: string;
  /** Live-stats field this cell binds to; live value (when present) overrides `value`. */
  statKey: LandingStatKey;
  value: number;
  suffix?: string;
  label: string;
  caption: string;
  narrative: string;
  /** Optional secondary link — e.g. sessions count → Studio set list. */
  detailHref?: string;
  detailLabel?: string;
};

/** Real project scale — Apple-style proof section (numbers first). */
export const LANDING_SCALE_INTRO = {
  eyebrow: "Scale",
  title: "真实项目，真实规模。",
  subtitle: "不是 demo——一个持续运行、不断归档的现场摄影系统，下面是它跑过的真实数据。",
} as const;

export const LANDING_SCALE_STATS: LandingScaleStat[] = [
  {
    id: "sessions",
    statKey: "sessions_total",
    value: 50,
    suffix: "+",
    label: "Live Sessions",
    caption: "已归档的现场场次",
    narrative: "每场 Live 独立建档，含时间线与文件索引。",
    detailHref: `${STUDIO_HOME}#sessions`,
    detailLabel: "Browse sessions in Studio",
  },
  {
    id: "archived",
    statKey: "photos_total",
    value: 27000,
    suffix: "+",
    label: "Photos Archived",
    caption: "预览图与 RAW 成对留存",
    narrative: "导出时 Previews 与同级 RAW 目录配对交付。",
  },
  {
    id: "evaluations",
    statKey: "analyzed_photos_total",
    value: 8000,
    suffix: "+",
    label: "AI Evaluations",
    caption: "完成的 AI 美学评估",
    narrative: "评分先做粗筛，人工再确认。",
  },
  {
    id: "brain",
    statKey: "exported_photos_total",
    value: 573,
    label: "Photos In Brain DB",
    caption: "纳入记忆库的精选记录",
    narrative: "入选结果沉淀进可查询的 Brain DB。",
  },
];

export type NavLink = {
  label: string;
  href: string;
  description?: string;
};

/**
 * Landing top nav (left → right)
 *
 * | Label    | Anchor     | Page section                          |
 * |----------|------------|---------------------------------------|
 * | Features | #features  | Hero + proof stats (what Luma does)   |
 * | Workflow | #workflow  | From Capture To Delivery journey      |
 * | Gallery  | #gallery   | Exported image showcase strip         |
 * | Docs     | #docs      | Documentation & getting started       |
 *
 * Right CTA: Open Studio → /studio
 */
export const LANDING_NAV: NavLink[] = [
  { label: "Features", href: "#features", description: "产品能力与累计处理量" },
  { label: "Workflow", href: "#workflow", description: "现场摄影全流程" },
  { label: "Gallery", href: "#gallery", description: "真实选片交付样例" },
  { label: "Docs", href: "#docs", description: "文档与上手说明" },
];

export type WorkflowStep = {
  id: string;
  title: string;
  tagline: string;
};

export const LANDING_WORKFLOW = {
  eyebrow: "Workflow",
  title: "From Capture To Delivery",
  subtitle: "从 SD 卡插入到 Gallery 导出，每一步都有明确的产物与状态。",
  phases: [
    { id: "capture", label: "Capture", range: [0, 0] },
    { id: "process", label: "Process", range: [1, 4] },
    { id: "deliver", label: "Deliver", range: [5, 6] },
  ],
  steps: [
    { id: "ingest", title: "Ingest", tagline: "Go 程序扫描存储卡，建立场次与文件索引。" },
    { id: "seed-jobs", title: "Create Jobs", tagline: "按场次生成处理作业，写入队列。" },
    { id: "run-job", title: "run_job", tagline: "Worker 领取作业，进入执行。" },
    { id: "pipeline-runner", title: "Pipeline Runner", tagline: "按序运行 Stage 1–3，逐阶段产出结果。" },
    { id: "inference", title: "Inference", tagline: "VLM 读图，生成 Caption、Tags 与评分。" },
    { id: "artifacts", title: "Artifacts", tagline: "结果写入 analysis_results.json，可追溯回看。" },
    { id: "gallery", title: "Gallery", tagline: "在 Gallery 选片、试色、批量导出。" },
  ] satisfies WorkflowStep[],
} as const;

export type BrainEntity = {
  id: string;
  label: string;
  caption: string;
  countKey: keyof LandingBrainCounts;
};

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

export const LANDING_BRAIN = {
  id: "brain",
  eyebrow: "Brain",
  title: "Every Action Leaves A Trace",
  subtitle: "作业、事件、产物、场次、照片——全部写入同一套可查询的数据模型。",
  manifesto: ["Every inference", "Every artifact", "Every decision", "is traceable."],
  entities: [
    { id: "jobs", label: "Jobs", caption: "工作单元与生命周期", countKey: "jobs" },
    { id: "events", label: "Events", caption: "状态迁移与审计事件", countKey: "events" },
    { id: "artifacts", label: "Artifacts", caption: "分析结果与导出文件", countKey: "artifacts" },
    { id: "sessions", label: "Sessions", caption: "现场档位与时间线", countKey: "sessions" },
    { id: "photos", label: "Photos", caption: "单张照片的入库与结论", countKey: "photos" },
    { id: "snapshots", label: "Snapshots", caption: "运行时快照与观测", countKey: "snapshots" },
  ] satisfies BrainEntity[],
  infraHref: "/infra/brain",
} as const;

export type LandingInfraMetrics = {
  queue_depth: number;
  workers_online: number;
  workers_total: number;
  retry_pending: number;
  recovery_requeues: number;
  pipeline_active: number;
  monitoring_snapshots: number;
  dead_letter: number;
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
};

export type InfraPillar = {
  id: string;
  label: string;
  caption: string;
  metricKey: keyof LandingInfraMetrics;
};

export const LANDING_INFRA = {
  id: "infra",
  eyebrow: "Infra",
  title: "Built For Long Running Workloads",
  subtitle: "单场处理动辄数小时——队列、重试、worker 恢复与运行时观测，为长任务而设计。",
  highlights: [
    {
      id: "recovery",
      title: "Worker Recovery",
      description: "Worker 离线、DRAINING、重新上线——工作不丢，状态可续。",
    },
    {
      id: "retry",
      title: "Retry Mechanism",
      description: "失败可重试、attempt 可追踪，永久失败才进入 dead letter。",
    },
    {
      id: "scheduling",
      title: "Job Scheduling",
      description: "按 executor pool 准入，pipeline 阶段有序推进。",
    },
    {
      id: "queue",
      title: "Queue Management",
      description: "队列深度、排队等待、inflight 上限——看得见背压。",
    },
  ],
  pillars: [
    { id: "queue", label: "Queue", caption: "排队中的作业", metricKey: "queue_depth" },
    { id: "workers", label: "Workers", caption: "在线 worker / 总数", metricKey: "workers_online" },
    { id: "retry", label: "Retry", caption: "待重试作业", metricKey: "retry_pending" },
    { id: "recovery", label: "Recovery", caption: "已重新入队", metricKey: "recovery_requeues" },
    { id: "monitoring", label: "Monitoring", caption: "运行时快照", metricKey: "monitoring_snapshots" },
  ] satisfies InfraPillar[],
  consoleHref: "/infra",
} as const;

export type ProductMatrixItem = {
  id: string;
  name: string;
  role: string;
  description: string;
  href: string;
  showcaseHref?: string;
  featured?: boolean;
};

export const LANDING_PRODUCT_MATRIX = {
  id: "products",
  eyebrow: "Product",
  title: "不止一个页面。",
  subtitle: "这是一套完整系统：工作台、选片、记忆与基础设施，各司其职，彼此衔接。",
  products: [
    {
      id: "studio",
      name: "Studio",
      role: "Workbench",
      description: "管理场次、运行 pipeline、从这里进入每一次现场工作。",
      href: STUDIO_HOME,
      featured: true,
    },
    {
      id: "gallery",
      name: "Gallery",
      role: "Curation",
      description: "看评分、调口味、选片导出——交付发生在这里。",
      href: "/gallery",
      showcaseHref: "#gallery",
    },
    {
      id: "brain",
      name: "Brain",
      role: "Memory",
      description: "每一次推理与决定都被记录——可追溯、可查询、可沉淀。",
      href: "/infra/brain",
      showcaseHref: "#brain",
    },
    {
      id: "infra",
      name: "Infra",
      role: "Control plane",
      description: "队列、worker、重试与观测——为长时间运行的现场任务而建。",
      href: "/infra",
      showcaseHref: "#infra",
    },
  ] satisfies ProductMatrixItem[],
} as const;

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
  eyebrow: "AI Layer",
  title: "AI 读懂了每一张照片。",
  subtitle: "图像送入视觉语言模型，输出画面描述、语义标签与美学评分。Gallery 里的每条信息，都来自这条多模态链路。",
  flow: [
    { id: "image", label: "Image", tagline: "现场预览帧输入" },
    { id: "vlm", label: "VLM", tagline: "多模态读图与理解" },
    { id: "caption", label: "Caption", tagline: "画面内容的一句话描述" },
    { id: "tags", label: "Tags", tagline: "可检索的语义标签" },
    { id: "score", label: "Score", tagline: "美学与构图评分" },
  ] satisfies AiFlowStep[],
  stages: [
    {
      stage: "01",
      title: "Stage 1",
      name: "OpenCV",
      body: "信号与构图语义，毫秒级预筛。",
    },
    {
      stage: "02",
      title: "Stage 2",
      name: "Rule Filter",
      body: "规则与快速美学打分，缩小候选集。",
    },
    {
      stage: "03",
      title: "Stage 3",
      name: "Vision Language Model",
      body: "多模态深读——Caption 与 Tags 在此生成。",
    },
  ] satisfies AiPipelineStage[],
  preview: {
    caption: "歌手特写，表情张力强，舞台灯光层次清晰。",
    tags: ["peak moment", "front row"],
    score: "8.7",
    dimensions: "E 9.1 · T 8.4 · C 8.6",
  },
} as const;

export const LANDING_DOC_LINKS: NavLink[] = [
  { label: "Quick start", href: "#", description: "从 SD 入库到第一次 Gallery 选片" },
  { label: "Pipeline overview", href: "#", description: "Stage 1–3 与 VLM 分析说明" },
  { label: "Gallery & export", href: "#", description: "选片、口味偏好、胶片 Lab、导出" },
  { label: "GitHub", href: "#" },
];

export const LANDING_FOOTER_COLUMNS: { title: string; links: NavLink[] }[] = [
  {
    title: "Workbench",
    links: [
      { label: "Studio", href: "/studio" },
      { label: "Gallery", href: "/gallery" },
      { label: "Settings", href: "/config" },
    ],
  },
  {
    title: "Product",
    links: [
      { label: "Features", href: "#features" },
      { label: "Products", href: "#products" },
      { label: "Workflow", href: "#workflow" },
      { label: "AI Layer", href: "#ai-layer" },
      { label: "Brain", href: "#brain" },
      { label: "Luma Personal", href: "/personal" },
    ],
  },
  {
    title: "Resources",
    links: [
      { label: "Docs", href: "#docs" },
      { label: "Documentation", href: "#" },
      { label: "GitHub", href: "#" },
    ],
  },
  {
    title: "Operators",
    links: [
      { label: "Infra showcase", href: "#infra" },
      { label: "Infra console", href: "/infra" },
    ],
  },
];

/** Studio app shell — primary work routes. */
export const STUDIO_PRIMARY_NAV: NavLink[] = [
  { label: "Sessions", href: "/studio" },
  { label: "Gallery", href: "/gallery" },
  { label: "Settings", href: "/config" },
];

/** Studio app shell — secondary / operator routes. */
export const STUDIO_SECONDARY_NAV: NavLink[] = [
  { label: "Site", href: "/" },
  { label: "Infra", href: "/infra" },
];
