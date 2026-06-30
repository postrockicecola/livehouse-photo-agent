export type QueueBacklog = {
  totals: {
    active: number;
    reserved: number;
    scheduled: number;
    redis_list_len: number | null;
  };
  workers: Array<{
    worker: string;
    active: number;
    reserved: number;
    scheduled: number;
  }>;
  redis_error?: string | null;
};

/** Optional per-style renders; paths are gallery-server paths (quoted by API). */
export type GalleryStyleVariant = {
  id: string;
  label: string;
  path_quoted: string;
  score?: number;
};

/** One row for ``POST /api/export-images`` ``items`` (optional; legacy ``images[]`` still works). */
export type GalleryExportItem = {
  file: string;
  rotate?: number;
  film_variant?: string | null;
  film_source_path_quoted?: string | null;
  alternate_jpeg_path_quoted?: string | null;
  /** When ``film_variant === "film_automated"``: per-image VLM grade params. */
  automated_adjust?: Record<string, number> | null;
};

export type GalleryItem = {
  file?: string;
  path?: string;
  path_quoted?: string;
  before_path?: string;
  before_path_quoted?: string;
  /** Extra graded looks (Film / BW / …); merged into preview strip when present. */
  style_variants?: GalleryStyleVariant[];
  orientation?: "landscape" | "portrait" | "square" | string;
  rotate_degrees?: number;
  width?: number;
  height?: number;
  overall_score?: number;
  energy?: number;
  technical?: number;
  composition?: number;
  algorithm_version?: string;
  /** Stage 3 / VLM 简评（若有）。 */
  reason?: string;
  reason_bilingual?: { en?: string; zh?: string };
  tags?: string[];
  category?: string;
  /** Stage4 数值修图建议（曝光/阴影/高光…），驱动「Automated」预览与导出。 */
  editing_adjustments?: Record<string, number> | null;
};
