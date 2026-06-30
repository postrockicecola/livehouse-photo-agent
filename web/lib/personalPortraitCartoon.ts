const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

export type ComfyHealth = {
  backend?: string;
  config_path?: string;
  comfy_reachable: boolean;
  comfy_url: string;
  workflow_configured: boolean;
  workflow_path: string;
  checkpoint_name: string;
  ready: boolean;
};

export type PortraitJobStatus = {
  ok: boolean;
  job_id: string;
  status: string;
  message?: string;
  error?: string;
  seed?: number;
  output_url?: string;
};

export async function fetchComfyHealth(): Promise<ComfyHealth> {
  const r = await fetch(`${API_BASE}/api/personal/portrait-cartoon/health`, { cache: "no-store" });
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export type GenerationMode = "likeness" | "balanced" | "scene" | "face_only";

export async function createPortraitCartoonJob(
  file: File,
  prompt: string,
  denoise?: number,
  seed?: number,
  generationMode?: GenerationMode,
): Promise<{ job_id: string; status: string; poll_url: string; seed?: number }> {
  const fd = new FormData();
  fd.append("image", file);
  fd.append("prompt", prompt);
  if (denoise != null && Number.isFinite(denoise)) {
    fd.append("denoise", String(denoise));
  }
  if (seed != null && Number.isFinite(seed)) {
    fd.append("seed", String(Math.trunc(seed)));
  }
  if (generationMode) {
    fd.append("generation_mode", generationMode);
  }
  const r = await fetch(`${API_BASE}/api/personal/portrait-cartoon/jobs`, {
    method: "POST",
    body: fd,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    throw new Error((data as { detail?: string }).detail || `create job ${r.status}`);
  }
  return data as { job_id: string; status: string; poll_url: string; seed?: number };
}

export async function fetchPortraitJob(jobId: string): Promise<PortraitJobStatus> {
  const r = await fetch(`${API_BASE}/api/personal/portrait-cartoon/jobs/${jobId}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`poll ${r.status}`);
  return r.json();
}

export function absoluteOutputUrl(outputUrl: string): string {
  if (outputUrl.startsWith("http")) return outputUrl;
  return `${API_BASE}${outputUrl}`;
}
