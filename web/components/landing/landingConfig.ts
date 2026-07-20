/** Gallery product showcase on the marketing page. */
export const LANDING_GALLERY_SECTION = {
  id: "gallery",
  eyebrow: "Gallery",
  title: "读 AI 结果，人做最终确认。",
  subtitle: "分数和标签来自上游 VLM；这里做人工确认，也可以开 ChatDock 问场次。",
} as const;

export type LandingGalleryFeature = {
  id: string;
  label: string;
  description: string;
};

export const LANDING_GALLERY_FEATURES: LandingGalleryFeature[] = [
  { id: "score", label: "VLM 评分", description: "整体分 · Energy · Technical · Composition" },
  { id: "tags", label: "结构化标签", description: "VLM 语义字段，可检索" },
  { id: "style", label: "风格预览", description: "胶片 Lab 实时试色" },
  { id: "select", label: "人工确认", description: "点选、标记、偏好反馈" },
  { id: "export", label: "导出交付", description: "预览与 RAW 批量导出" },
];

/** Demo metadata overlaid on showcase tiles (marketing mock). */
export const LANDING_GALLERY_MOCK_META = [
  {
    file: "DSC05641.jpg",
    score: 8.7,
    energy: 9.1,
    technical: 8.4,
    composition: 8.6,
    tags: ["peak moment", "front row"],
    aiLine: "歌手特写，表情张力强，舞台灯光层次清晰。",
    selected: true,
    exportStyle: "Cinestill 800T",
  },
  {
    file: "DSC05870.jpg",
    score: 7.9,
    energy: 8.2,
    technical: 7.6,
    composition: 8.0,
    tags: ["wide", "crowd"],
    aiLine: "全场氛围，灯海与舞台形成对比。",
    selected: false,
    exportStyle: null,
  },
  {
    file: "DSC06013.jpg",
    score: 8.3,
    energy: 8.8,
    technical: 8.0,
    composition: 7.9,
    tags: ["motion", "backlit"],
    aiLine: "逆光轮廓，动感模糊恰到好处。",
    selected: true,
    exportStyle: "Portra 400",
  },
  {
    file: "DSC06201.jpg",
    score: 7.4,
    energy: 7.1,
    technical: 7.8,
    composition: 7.3,
    tags: ["drummer", "low light"],
    aiLine: "鼓手局部，低照度仍保留细节。",
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
