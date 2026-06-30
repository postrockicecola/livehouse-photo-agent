export type TasteProfile = {
  version?: number;
  method?: string;
  n_liked?: number;
  n_rest?: number;
  dim_weights?: Record<string, number>;
  mean_liked?: Record<string, number>;
  exemplars?: Array<{ file?: string; overall_score?: number }>;
  updated_unix?: number;
};

export type TasteGetResponse = {
  active: boolean;
  profile: TasteProfile | null;
  few_shot_preview?: string;
  previews_dir?: string;
};

export async function fetchTasteProfile(apiBase: string): Promise<TasteGetResponse> {
  const res = await fetch(`${apiBase}/api/gallery/taste`, { cache: "no-store" });
  if (!res.ok) throw new Error(`读取口味模型失败 HTTP ${res.status}`);
  return res.json() as Promise<TasteGetResponse>;
}

export async function rebuildTasteProfile(apiBase: string): Promise<Record<string, unknown>> {
  const res = await fetch(`${apiBase}/api/gallery/taste/rebuild`, { method: "POST" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(
      typeof data?.error === "string" ? data.error : `重建口味模型失败 HTTP ${res.status}`,
    );
  }
  return data as Record<string, unknown>;
}

/** Top dimension deltas for a one-line UI hint. */
export function tasteTopHints(profile: TasteProfile | null, limit = 3): string[] {
  const w = profile?.dim_weights;
  if (!w) return [];
  const entries = Object.entries(w)
    .filter(([, v]) => typeof v === "number" && Math.abs(v) >= 0.2)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]));
  const labels: Record<string, string> = {
    moment_peak: "瞬间",
    atmosphere_impact: "氛围",
    light_color_character: "光影色彩",
    focus_sharpness: "清晰",
    exposure_control: "曝光",
    composition_framing: "构图",
    deliverable_subject: "主体",
    noise_cleanliness: "干净度",
  };
  return entries.slice(0, limit).map(([k, v]) => {
    const lab = labels[k] ?? k;
    return v > 0 ? `偏好更高「${lab}」` : `偏好更低「${lab}」`;
  });
}
