/** Gallery product showcase on the marketing page. */
export const LANDING_GALLERY_SECTION = {
  id: "gallery",
  eyebrow: "产品结果",
  title: "读 AI 结果，也能对话着选。",
  subtitle:
    "流水线给出筛选、评分与标签；Gallery 里人工确认，并用对话 Agent 搜片、初选、试胶片风格、导出。界面分数字段为产品示意叠加（Simulated overlay），底图来自真实场次。",
} as const;

export type LandingGalleryFeature = {
  id: string;
  label: string;
  description: string;
};

export const LANDING_GALLERY_FEATURES: LandingGalleryFeature[] = [
  { id: "score", label: "VLM 评分", description: "整体分 · Energy · Technical · Composition" },
  { id: "tags", label: "结构化标签", description: "VLM 语义字段，可检索" },
  { id: "agent", label: "对话 Agent", description: "自然语言搜、选、定风格" },
  { id: "style", label: "风格预览", description: "胶片 / 梦核等 Showcase grade" },
  { id: "select", label: "人工确认", description: "点选、标记、偏好反馈" },
  { id: "export", label: "导出交付", description: "预览与 RAW 批量导出" },
];

/**
 * Demo frames for the Gallery product mock — path + overlay stay paired.
 * Paths are bundled showcase covers (see ``web/public/showcase/covers/``);
 * captions were written against those frames (not index-aligned API covers).
 */
export type LandingGalleryMockFrame = {
  path: string;
  file: string;
  score: number;
  energy: number;
  technical: number;
  composition: number;
  tags: readonly string[];
  aiLine: string;
  selected: boolean;
  exportStyle: string | null;
};

export const LANDING_GALLERY_MOCK_FRAMES: readonly LandingGalleryMockFrame[] = [
  {
    path: "/showcase/covers/session-57.jpg",
    file: "DSC09945.jpg",
    score: 8.7,
    energy: 9.1,
    technical: 8.4,
    composition: 8.6,
    tags: ["duo", "peak energy"],
    aiLine: "吉他手举拳与主唱同框，青绿薄雾里张力拉满。",
    selected: true,
    exportStyle: "Cinestill 800T",
  },
  {
    path: "/showcase/covers/session-56.jpg",
    file: "DSC07563.jpg",
    score: 8.3,
    energy: 8.8,
    technical: 8.0,
    composition: 7.9,
    tags: ["vocalist", "spotlight"],
    aiLine: "主唱立于蓝雾聚光中，前景观众剪影压住舞台层次。",
    selected: true,
    exportStyle: "Portra 400",
  },
  {
    path: "/showcase/covers/session-55.jpg",
    file: "DSC05257.jpg",
    score: 7.9,
    energy: 8.2,
    technical: 7.6,
    composition: 8.0,
    tags: ["wide", "crowd"],
    aiLine: "观众席全景：投影大字与青绿灯束切开舞台。",
    selected: false,
    exportStyle: null,
  },
  {
    path: "/showcase/covers/session-54.jpg",
    file: "DSC03267.jpg",
    score: 7.4,
    energy: 7.1,
    technical: 7.8,
    composition: 7.3,
    tags: ["drummer", "silhouette"],
    aiLine: "紫雾中鼓组与吉他手剪影，顶光灯束落下。",
    selected: false,
    exportStyle: null,
  },
] as const;

export const LANDING_GALLERY_STYLE_PRESETS = [
  { id: "plain", label: "原图" },
  { id: "cinestill", label: "Cinestill 800T", active: true },
  { id: "portra", label: "Portra 400" },
  { id: "bw", label: "Acros B&W" },
] as const;
