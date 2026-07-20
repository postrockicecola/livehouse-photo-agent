/**
 * Luma product information architecture
 *
 * Portfolio copy: plain, role-aligned (AI fullstack / AI infra / Agent).
 * Default path = Stage 1–3 analysis jobs + queue/workers.
 * Agent path   = ReAct curation + Gallery ChatDock.
 */

export const MARKETING_HOME = "/";
export const STUDIO_HOME = "/studio";

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
  eyebrow: "个人项目 · AI 全栈 / Infra / Agent",
  /** First-screen slogan — what this project is. */
  title: "看得见的 AI 工作流。",
  subtitle: "Go 入库，Python 跑 Stage / VLM，Next.js 做工作台。用 Live 摄影场次压真实数据量。",
  description:
    "默认链路：入库 → 建 job → Stage 1–3（OpenCV / 美学分 / VLM）→ 结构化结果 → Gallery 人工确认。另有 ReAct 选片 Agent 和 ChatDock。",
  ctaPrimary: LANDING_STUDIO_CTA,
  ctaSecondary: { label: "看处理链路", href: "#workflow" },
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
  eyebrow: "数据规模",
  title: "真实场次上的推理规模。",
  subtitle: "持续入库、排队推理后留下的统计，不是演示素材凑的。",
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

/**
 * Landing top nav
 * Pipeline → Infra → Agent → Fullstack surfaces
 */
export const LANDING_NAV: NavLink[] = [
  { label: "Pipeline", href: "#workflow", description: "作业与阶段处理" },
  { label: "Infra", href: "#infra", description: "队列、Worker、重试" },
  { label: "Agent", href: "#agent", description: "ReAct 选片与 ChatDock" },
  { label: "Stack", href: "#products", description: "前后端与运维界面" },
];

export type WorkflowStep = {
  id: string;
  title: string;
  tagline: string;
};

export const LANDING_WORKFLOW = {
  eyebrow: "Pipeline",
  title: "从入库到 VLM 结构化输出。",
  subtitle: "默认跑 ANALYZE 作业：建队、领取、分阶段处理、落盘。状态都能查。",
  phases: [
    { id: "ingest", label: "Ingest", range: [0, 0] },
    { id: "orchestrate", label: "Run", range: [1, 4] },
    { id: "deliver", label: "Deliver", range: [5, 6] },
  ],
  steps: [
    { id: "ingest", title: "Ingest", tagline: "Go 扫卡，建 session 和文件索引。" },
    { id: "seed-jobs", title: "Create Jobs", tagline: "按场次拆成 job，写入队列。" },
    { id: "run-job", title: "Claim & Run", tagline: "Worker 领任务并执行。" },
    { id: "pipeline-runner", title: "Pipeline Runner", tagline: "按 Stage 1–3 顺序跑，阶段结果可回看。" },
    { id: "inference", title: "Inference", tagline: "VLM 出 caption、tags、分数；并发有上限。" },
    { id: "artifacts", title: "Artifacts", tagline: "结果落盘，job / event / artifact 可追。" },
    { id: "gallery", title: "Gallery", tagline: "人在界面里确认选片和导出。" },
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
  subtitle: "一场 VLM 处理经常要跑很久。所以做了队列、Worker 恢复、重试和运行观测。",
  highlights: [
    {
      id: "recovery",
      title: "Worker 恢复",
      description: "Worker 掉线或 DRAINING 后可以再上线，任务尽量续跑。",
    },
    {
      id: "retry",
      title: "重试与死信",
      description: "失败可重试，attempt 可查；确认搞不定的进 dead letter。",
    },
    {
      id: "scheduling",
      title: "准入控制",
      description: "按 executor pool 控制并发，避免把推理端打满。",
    },
    {
      id: "queue",
      title: "队列与背压",
      description: "能看队列深度、排队情况和 inflight 上限。",
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
  eyebrow: "全栈界面",
  title: "AI 工作台到运维台。",
  subtitle: "Studio / Gallery / Brain / Infra——分别对应触发推理、确认结果、查账、看运行态。",
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
      description: "看分数和标签，人工确认导出；也可开 ChatDock。",
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
      description: "队列、Worker、重试，以及 Agent step 回放。",
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

/** ReAct curation + ChatDock — separate from default ANALYZE jobs. */
export const LANDING_AGENT = {
  id: "agent",
  eyebrow: "Agent",
  title: "ReAct 选片 Agent。",
  subtitle:
    "默认 Studio 跑 ANALYZE。CURATE_* 才走 plan → tool → reflect：决定下一张深读谁，直到 finalize 或预算用完。",
  honesty: "优先用 LLM planner，不行就 heuristic。每一步记进 job_events，Infra 可以回放。",
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
  { label: "处理链路", href: "#ai-layer", description: "Stage 1–3 和 VLM 输出" },
  { label: "选片 Agent", href: "#agent", description: "ReAct、tools、预算停止" },
  { label: "Infra", href: "#infra", description: "队列、Worker、重试、死信" },
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
      { label: "Pipeline", href: "#workflow" },
      { label: "推理阶段", href: "#ai-layer" },
      { label: "Infra", href: "#infra" },
      { label: "Agent", href: "#agent" },
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
  { label: "Infra", href: "/infra" },
];
