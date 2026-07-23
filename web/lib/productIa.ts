/**
 * Luma product information architecture
 *
 * Portfolio narrative (Batch A): product value first, then job-centric AI runtime.
 * Main path = ingest → cheap gates → durable jobs → bounded VLM → ledger → Gallery/Infra.
 * Agent / KEDA / RLHF / prompt labs = Infra Experiments (extensions), not co-equal headlines.
 */

export const MARKETING_HOME = "/";
export const STUDIO_HOME = "/studio";

/** Shared one-liner — keep README / landing / interview pitch aligned. */
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

/** Primary CTA — nav + hero + closing. */
export const LANDING_STUDIO_CTA = "打开 Studio";

/** Hero chat-box rotating prompts — concrete things the system can do. */
export const LANDING_HERO_PROMPTS = [
  "帮我从这场里选出 40 张能交片的",
  "把糊的、过曝的先剔掉",
  "连拍里每组只留一张最好的",
  "按分数从高到低给我一份初选",
  "找出吉他手弹琴的特写",
  "找出全景舞台、观众和灯都在的",
  "找出鼓手打鼓的那几张",
  "找出歌手表情最狠的瞬间",
  "找出前排互动、气氛最猛的",
  "找出逆光剪影那种",
  "修成复古胶片风预览看看",
  "试试 Cinestill 800T 的胶片感觉",
  "出一组黑白的，偏纪实",
  "选中的导出预览，RAW 也一起打包",
  "这场里 energy 最高的十张是哪些",
  "有没有技术分高但构图一般的，标出来",
] as const;

export const LANDING_HERO = {
  eyebrow: "个人项目 · Job-centric AI Runtime",
  /** First-screen slogan — product value before infra nouns. */
  title: "现场照片，变成可交付的选片结果。",
  subtitle:
    "低成本视觉门控 → 持久作业 → 有界 VLM → Gallery / Infra Console。摄影是真实业务负载，用来验证可恢复、可观察的推理作业系统。",
  description: PROJECT_POSITIONING.oneLinerZh,
  ctaPrimary: LANDING_STUDIO_CTA,
  ctaSecondary: { label: "看主链路", href: "#workflow" },
  promptIdle: "试试：找出吉他手特写…",
  promptSubmitHref: "/gallery",
  promptCtas: [
    { label: "打开 Studio", href: STUDIO_HOME },
    { label: "打开 Infra", href: "/infra" },
  ],
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
  detailHref?: string;
  detailLabel?: string;
};

export const LANDING_SCALE_INTRO = {
  eyebrow: "规模（次级）",
  title: "归档场次上的累计规模。",
  subtitle:
    "补充上下文，不是第一印象。优先 Live；不可达时为 Recorded 数量级，均带 provenance 标签。",
} as const;

export const LANDING_SCALE_STATS: LandingScaleStat[] = [
  {
    id: "sessions",
    statKey: "sessions_total",
    value: 50,
    suffix: "+",
    label: "Sessions",
    caption: "已建档场次",
    narrative: "每场独立建档，带时间线和文件索引。",
    detailHref: `${STUDIO_HOME}#sessions`,
    detailLabel: "在 Studio 查看场次",
  },
  {
    id: "archived",
    statKey: "photos_total",
    value: 27000,
    suffix: "+",
    label: "Photos",
    caption: "入库照片（预览 + RAW）",
    narrative: "导出时预览与 RAW 按目录配对。",
  },
  {
    id: "evaluations",
    statKey: "analyzed_photos_total",
    value: 8000,
    suffix: "+",
    label: "Analyzed",
    caption: "完成多阶段分析",
    narrative: "粗筛 → 打分 → VLM，再人工确认。",
  },
  {
    id: "brain",
    statKey: "exported_photos_total",
    value: 573,
    label: "In ledger",
    caption: "写入 Brain 的记录",
    narrative: "结果进可查询的作业账本。",
  },
];

export type NavLink = {
  label: string;
  href: string;
  description?: string;
};

/** Landing top nav — product surfaces only. */
export const LANDING_NAV: NavLink[] = [
  { label: "结果", href: "#outcome", description: "一次运行的交付指标" },
  { label: "画廊", href: "#gallery", description: "筛选与确认" },
  { label: "主链路", href: "#workflow", description: "门控 → 作业 → VLM" },
  { label: "评估", href: "#evaluation", description: "固定评估集对比" },
  { label: "Infra", href: "#infra", description: "队列、Worker、账本" },
  { label: "工作台", href: "#products", description: "Studio / Gallery / Console" },
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
    "场次先建成可恢复的作业，再经低成本门控进入有界 VLM；状态、调用与产物都写进账本，可在 Infra 里回看。",
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
    { id: "gallery", title: "Gallery", tagline: "人工确认选片并导出。" },
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
  eyebrow: "Data model",
  title: "每次推理都进账本。",
  subtitle: "Jobs、Events、Artifacts、Sessions——pipeline 步骤和 Agent step 都能查。",
  manifesto: ["推理有记录", "产物可回放", "状态可追查"],
  entities: [
    { id: "jobs", label: "Jobs", caption: "作业与生命周期", countKey: "jobs" },
    { id: "events", label: "Events", caption: "状态变更事件", countKey: "events" },
    { id: "artifacts", label: "Artifacts", caption: "分析结果与导出", countKey: "artifacts" },
    { id: "sessions", label: "Sessions", caption: "场次与时间线", countKey: "sessions" },
    { id: "photos", label: "Photos", caption: "单张入库与结论", countKey: "photos" },
    { id: "snapshots", label: "Snapshots", caption: "运行时快照", countKey: "snapshots" },
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
  eyebrow: "AI Infra",
  title: "推理作业的控制面。",
  subtitle: "看队列深度、作业时间线、模型调用与失败恢复——同一套 API 驱动执行与控制台。",
  highlights: [
    {
      id: "recovery",
      title: "Durable Jobs",
      description: "认领、重试、死信写在 SQL；Celery 结果不是权威状态。",
    },
    {
      id: "retry",
      title: "失败可恢复",
      description: "失败可重试，attempt 可查；确认搞不定的进 dead letter。",
    },
    {
      id: "scheduling",
      title: "Bounded Inference",
      description: "有界队列与准入控制，避免把推理端打满。",
    },
    {
      id: "queue",
      title: "可观察账本",
      description: "作业时间线、模型调用、成本与产物可 drill-down。",
    },
  ],
  pillars: [
    { id: "queue", label: "Queue", caption: "排队中的作业", metricKey: "queue_depth" },
    { id: "workers", label: "Workers", caption: "在线 / 总数", metricKey: "workers_online" },
    { id: "retry", label: "Retry", caption: "待重试", metricKey: "retry_pending" },
    { id: "recovery", label: "Recovery", caption: "重新入队", metricKey: "recovery_requeues" },
    { id: "monitoring", label: "Monitoring", caption: "运行快照", metricKey: "monitoring_snapshots" },
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
  eyebrow: "工作台",
  title: "从提交作业到确认交付。",
  subtitle: "Studio 触发分析，Gallery 确认选片，Brain 查账，Infra 看运行态。",
  products: [
    {
      id: "studio",
      name: "Studio",
      role: "工作台",
      description: "看场次、触发 ANALYZE、进 Gallery。",
      href: STUDIO_HOME,
      featured: true,
    },
    {
      id: "gallery",
      name: "Gallery",
      role: "选片界面",
      description: "看分数和标签，人工确认导出。",
      href: "/gallery",
      showcaseHref: "#gallery",
    },
    {
      id: "brain",
      name: "Brain",
      role: "数据账本",
      description: "查 job、事件和产物。",
      href: "/infra/brain",
      showcaseHref: "#brain",
    },
    {
      id: "infra",
      name: "Infra",
      role: "运维台",
      description: "队列、Worker、重试与模型调用归因。",
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
    caption: "歌手特写，表情张力强，舞台灯光层次清晰。",
    tags: ["peak moment", "front row"],
    score: "8.7",
    dimensions: "E 9.1 · T 8.4 · C 8.6",
  },
} as const;

export type AgentLoopStep = {
  id: string;
  label: string;
  tagline: string;
};

export type AgentTool = {
  id: string;
  name: string;
  description: string;
};

export type AgentSurface = {
  id: string;
  title: string;
  description: string;
  href: string;
  cta: string;
};

/** Optional curation loop + ChatDock (shown in Infra / Gallery, not as a landing pitch). */
export const LANDING_AGENT = {
  id: "agent",
  eyebrow: "Agent",
  title: "预算内的选片循环。",
  subtitle: "在候选集上 inspect / analyze / finalize，把每一步决策记进作业时间线。",
  honesty: "步数与推理次数有上限；异常输出时回退到确定性策略。",
  loop: [
    { id: "observe", label: "Observe", tagline: "看候选和已有分数" },
    { id: "plan", label: "Plan", tagline: "选下一个 tool" },
    { id: "act", label: "Act", tagline: "inspect / analyze / …" },
    { id: "reflect", label: "Reflect", tagline: "需要的话升级再看" },
    { id: "finalize", label: "Finalize", tagline: "给出 keepers 并结束" },
  ] satisfies AgentLoopStep[],
  tools: [
    { id: "inspect", name: "inspect", description: "看元数据和粗信号" },
    { id: "analyze", name: "analyze", description: "按 tier 调 VLM" },
    { id: "compare", name: "compare", description: "并排比较" },
    { id: "cluster", name: "cluster", description: "合并连拍近似图" },
    { id: "query", name: "query_gallery", description: "复用历史分析" },
    { id: "finalize", name: "finalize", description: "出选片结果" },
  ] satisfies AgentTool[],
  surfaces: [
    {
      id: "runs",
      title: "Agent runs",
      description: "在 Infra 看 step、LLM/heuristic 比例、keepers。",
      href: "/infra",
      cta: "打开 Infra →",
    },
    {
      id: "chat",
      title: "ChatDock",
      description: "在 Gallery 问场次：读分数和 keep/trash，不改文件。",
      href: "/gallery",
      cta: "打开 Gallery →",
    },
  ] satisfies AgentSurface[],
  guards: ["max_steps", "inference budget", "FINALIZE"],
} as const;

export const LANDING_DOC_LINKS: NavLink[] = [
  { label: "上手", href: "#", description: "从入库到第一次 Gallery 选片" },
  { label: "主链路", href: "#workflow", description: "门控 → 作业 → 有界 VLM" },
  { label: "Evaluation", href: "/eval", description: "Stage3 / Agent 基线与出处" },
  { label: "Infra", href: "#infra", description: "队列、Worker、重试、死信" },
  { label: "Gallery", href: "#gallery", description: "读结果并确认选片" },
];

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
      { label: "主链路", href: "#workflow" },
      { label: "推理阶段", href: "#ai-layer" },
      { label: "Infra", href: "#infra" },
      { label: "Gallery", href: "#gallery" },
      { label: "Brain", href: "#brain" },
      { label: "Personal", href: "/personal" },
    ],
  },
  {
    title: "资源",
    links: [
      { label: "Docs", href: "#docs" },
      { label: "Documentation", href: "#" },
      { label: "GitHub", href: "#" },
    ],
  },
  {
    title: "运维",
    links: [
      { label: "Infra 说明", href: "#infra" },
      { label: "Infra 控制台", href: "/infra" },
      { label: "Brain 控制台", href: "/infra/brain" },
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
  { label: "Eval", href: "/eval" },
  { label: "Infra", href: "/infra" },
];
