export type PersonalFeature = {
  slug: string;
  title: string;
  description: string;
  /** 已上线：点击进入真实页面 */
  available: boolean;
  href: string;
  badge?: string;
};

export const PERSONAL_FEATURES: PersonalFeature[] = [
  {
    slug: "layout-sheet",
    title: "图片排版",
    description: "上传照片，按 A4 网格自动分页排版，适合冲印单与相册页预览。",
    available: true,
    href: "/personal/layout-sheet",
    badge: "可用",
  },
  {
    slug: "portrait-cartoon",
    title: "肖像卡通化",
    description: "上传肖像 + 文字描述，本机 ComfyUI 生成同风格卡通图（数据不出本机）。",
    available: true,
    href: "/personal/portrait-cartoon",
    badge: "Comfy",
  },
  {
    slug: "presets",
    title: "预设滤镜",
    description: "一键套用胶片 / 冷暖预设，轻量调色（筹备中）。",
    available: false,
    href: "/personal/presets",
  },
  {
    slug: "crop-export",
    title: "裁剪与导出",
    description: "比例裁剪、分辨率与格式导出（筹备中）。",
    available: false,
    href: "/personal/crop-export",
  },
  {
    slug: "batch",
    title: "批量处理",
    description: "多图统一尺寸、水印与重命名（筹备中）。",
    available: false,
    href: "/personal/batch",
  },
];

export function getPersonalFeature(slug: string): PersonalFeature | undefined {
  return PERSONAL_FEATURES.find((f) => f.slug === slug);
}
