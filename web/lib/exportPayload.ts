import type { GalleryExportItem } from "@/components/types";

/** 供 ``POST /api/export-images`` 使用：只含后端认识的字段，且绝不省略 ``file`` / ``rotate``。 */
export function serializeExportRequestBody(
  rows: GalleryExportItem[],
  category = "best",
  options?: { useSessionVibe?: boolean },
): string {
  const items = rows.map((row, idx) => {
    const file = String(row.file ?? "").trim();
    if (!file) {
      throw new Error(`导出第 ${idx + 1} 行缺少 file（内部数据异常）`);
    }
    let rot = Number(row.rotate ?? 0);
    if (!Number.isFinite(rot)) rot = 0;
    rot = Math.trunc(rot);
    const out: Record<string, string | number> = { file, rotate: rot };
    const fv = typeof row.film_variant === "string" ? row.film_variant.trim() : "";
    if (fv) {
      out.film_variant = fv;
      const fs = typeof row.film_source_path_quoted === "string" ? row.film_source_path_quoted.trim() : "";
      if (fs) out.film_source_path_quoted = fs;
    }
    const alt = typeof row.alternate_jpeg_path_quoted === "string" ? row.alternate_jpeg_path_quoted.trim() : "";
    if (alt && !fv) out.alternate_jpeg_path_quoted = alt;
    return out;
  });
  const images = items.map((it) => String(it.file));
  const body: Record<string, unknown> = {
    category,
    images,
    items,
  };
  if (options?.useSessionVibe) {
    body.use_session_vibe = true;
  }
  return JSON.stringify(body);
}
