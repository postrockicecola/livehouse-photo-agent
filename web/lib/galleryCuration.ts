import type { GalleryExportItem } from "@/components/types";
import { gallerySelectionKey } from "@/lib/defaultFilmExport";

/** Server also appends batch exports to ``Previews/runtime/export_feedback.json`` (strong supervision; not synced here). */

/** Matches ``utils.gallery_curation.VERDICTS``. */
export type CurationVerdict = "liked" | "pass" | "rejected";

/** Order: core six first, then extended (matches livehouse / Stage3 mapping). */
export const CURATION_LIKE_REASONS = [
  "moment",
  "atmosphere",
  "light",
  "composition",
  "subject",
  "editable",
  "expression",
  "energy",
  "color",
  "clarity",
  "exposure_fit",
  "narrative",
] as const;

export const CURATION_LIKE_REASON_LABELS: Record<CurationLikeReason, string> = {
  moment: "瞬间",
  atmosphere: "氛围",
  light: "光影",
  composition: "构图",
  subject: "主体",
  editable: "可后期",
  expression: "表情神态",
  energy: "现场张力",
  color: "色彩",
  clarity: "清晰",
  exposure_fit: "曝光舒服",
  narrative: "叙事感",
};

export const CURATION_REJECT_REASONS = [
  "blur_bad",
  "subject_bad",
  "light_bad",
  "composition_bad",
  "duplicate",
  "emotion_weak",
  "timing_bad",
  "obstructed",
  "background_bad",
  "exposure_bad",
  "distracting",
] as const;

export const CURATION_REJECT_REASON_LABELS: Record<CurationRejectReason, string> = {
  blur_bad: "模糊",
  subject_bad: "主体差",
  light_bad: "光影差",
  composition_bad: "构图差",
  duplicate: "重复",
  emotion_weak: "情绪弱",
  timing_bad: "时机不对",
  obstructed: "遮挡",
  background_bad: "背景乱",
  exposure_bad: "曝光翻车",
  distracting: "干扰元素",
};

export type CurationLikeReason = (typeof CURATION_LIKE_REASONS)[number];
export type CurationRejectReason = (typeof CURATION_REJECT_REASONS)[number];

export type CurationFeedbackEntry = {
  verdict: CurationVerdict;
  like_reasons?: CurationLikeReason[];
  reject_reasons?: CurationRejectReason[];
  note?: string;
};

export type GalleryCurationState = {
  version?: number;
  /** Derived: all keys with ``verdict === "liked"`` (legacy clients). */
  selected_keys: string[];
  feedback_by_key: Record<string, CurationFeedbackEntry>;
  export_by_file: Record<string, GalleryExportItem>;
  updated_unix?: number;
};

export type GalleryCurationGetResponse = {
  active: boolean;
  curation: GalleryCurationState | null;
  previews_dir?: string;
};

export type GalleryCurationSavePayload = {
  selected_keys?: string[];
  feedback_by_key?: Record<string, CurationFeedbackEntry>;
  export_by_file: Record<string, GalleryExportItem>;
};

/**
 * Map legacy basename keys → full paths when ``items`` is available (v1 curation).
 */
export function remapFeedbackKeysToGalleryItems(
  feedback: Record<string, CurationFeedbackEntry>,
  items: Array<{ file?: string; path?: string; path_quoted?: string }>,
): Record<string, CurationFeedbackEntry> {
  const pathByFile = new Map<string, string>();
  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const pk = gallerySelectionKey(it, i);
    const f = it.file?.trim();
    if (f && pk && !pathByFile.has(f)) pathByFile.set(f, pk);
  }
  const out: Record<string, CurationFeedbackEntry> = {};
  for (const [k, e] of Object.entries(feedback)) {
    if (!e?.verdict) continue;
    const mapped = pathByFile.get(k) ?? k;
    const existing = out[mapped];
    if (!existing || e.verdict === "liked") out[mapped] = { ...e };
  }
  return out;
}

/** Merge API v2 + legacy ``selected_keys`` into a single feedback map. */
export function hydrateFeedbackFromCuration(
  curation: GalleryCurationState | null | undefined,
  items?: Array<{ file?: string; path?: string; path_quoted?: string }>,
): Record<string, CurationFeedbackEntry> {
  const out: Record<string, CurationFeedbackEntry> = {};
  if (!curation) return out;
  let fb: Record<string, CurationFeedbackEntry> = { ...(curation.feedback_by_key ?? {}) };
  if (items?.length) {
    fb = remapFeedbackKeysToGalleryItems(fb, items);
  }
  for (const [k, e] of Object.entries(fb)) {
    if (k && e?.verdict) out[k] = { ...e };
  }
  for (const k of curation.selected_keys ?? []) {
    const sk = k.trim();
    if (!sk || out[sk]) continue;
    if (sk.includes("/")) {
      out[sk] = { verdict: "liked" };
      continue;
    }
    if (items?.length) {
      const mapped = remapFeedbackKeysToGalleryItems({ [sk]: { verdict: "liked" } }, items);
      const pathKey = Object.keys(mapped)[0];
      if (pathKey && !out[pathKey]) out[pathKey] = mapped[pathKey];
    } else {
      out[sk] = { verdict: "liked" };
    }
  }
  return out;
}

export function likedKeysFromFeedback(
  feedbackByKey: Record<string, CurationFeedbackEntry>,
): string[] {
  return Object.entries(feedbackByKey)
    .filter(([, e]) => e.verdict === "liked")
    .map(([k]) => k);
}

export function buildCurationSavePayload(
  feedbackByKey: Record<string, CurationFeedbackEntry>,
  exportByFile: Record<string, GalleryExportItem>,
): GalleryCurationSavePayload {
  return {
    selected_keys: likedKeysFromFeedback(feedbackByKey),
    feedback_by_key: feedbackByKey,
    export_by_file: exportByFile,
  };
}

export function setFeedbackVerdict(
  prev: Record<string, CurationFeedbackEntry>,
  key: string,
  verdict: CurationVerdict | null,
): Record<string, CurationFeedbackEntry> {
  const k = key.trim();
  if (!k) return prev;
  const next = { ...prev };
  if (verdict === null) {
    delete next[k];
    return next;
  }
  const existing = next[k];
  next[k] = {
    verdict,
    like_reasons: verdict === "liked" ? existing?.like_reasons ?? [] : undefined,
    reject_reasons: verdict === "rejected" ? existing?.reject_reasons ?? [] : undefined,
  };
  return next;
}

export function toggleFeedbackLikeReason(
  prev: Record<string, CurationFeedbackEntry>,
  key: string,
  reason: CurationLikeReason,
): Record<string, CurationFeedbackEntry> {
  const k = key.trim();
  if (!k) return prev;
  const existing = prev[k];
  if (!existing || existing.verdict !== "liked") {
    return prev;
  }
  const reasons = [...(existing.like_reasons ?? [])];
  const i = reasons.indexOf(reason);
  if (i >= 0) reasons.splice(i, 1);
  else reasons.push(reason);
  return { ...prev, [k]: { ...existing, like_reasons: reasons } };
}

export async function fetchGalleryCuration(apiBase: string): Promise<GalleryCurationGetResponse> {
  const res = await fetch(`${apiBase}/api/gallery/curation`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`读取选图状态失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<GalleryCurationGetResponse>;
}

export async function saveGalleryCuration(
  apiBase: string,
  payload: GalleryCurationSavePayload,
): Promise<GalleryCurationGetResponse> {
  const res = await fetch(`${apiBase}/api/gallery/curation`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || `保存选图状态失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<GalleryCurationGetResponse>;
}

export async function clearGalleryCuration(apiBase: string): Promise<GalleryCurationGetResponse> {
  const res = await fetch(`${apiBase}/api/gallery/curation`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clear: true }),
  });
  if (!res.ok) {
    throw new Error(`清除选图状态失败 HTTP ${res.status}`);
  }
  return res.json() as Promise<GalleryCurationGetResponse>;
}
