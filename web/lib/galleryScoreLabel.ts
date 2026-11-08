import type { GalleryItem } from "@/components/types";

export function galleryScoreLabel(item: Pick<GalleryItem, "score_status" | "stage3_meta" | "analysis_pending" | "overall_score">): string {
  if (item.score_status === "rejected") return "未通过初筛";
  const outcome = item.stage3_meta?.outcome;
  if (outcome === "skipped_stage3_gating") return "未送审 · 候选预算";
  if (outcome === "skipped_near_duplicate") return "未送审 · 重复照片";
  if (outcome === "vlm_error" || outcome === "fallback_defaults") return "评分失败";
  if (item.analysis_pending) return "待评分";
  if (item.score_status === "unscored") return "未评分";
  return Number(item.overall_score ?? 0).toFixed(1);
}
