import { getApiBase } from "@/lib/apiBase";

const API_BASE = getApiBase();

/** Photography funnel counts for session-card hover (remaining after each gate). */
export type StudioSessionFunnel = {
  imported?: number | null;
  filtered?: number | null;
  scored?: number | null;
  picked?: number | null;
  exported?: number | null;
};

export type StudioSessionRow = {
  session_key: string;
  session_dir: string;
  previews_dir: string;
  preview_count: number;
  has_analysis_results: boolean;
  brain_session_id: number | null;
  cover_path_quoted?: string;
  /** Optional portrait cover for narrow viewports (showcase). */
  cover_portrait_path_quoted?: string;
  /** Showcase / catalog date (YYYY-MM-DD); preferred over parsing session_key. */
  session_date?: string;
  /** Act / band name for hover + hero (public on Vercel showcase). */
  band_name?: string;
  venue?: string;
  /** Imported → Filtered → Scored → Picked → Exported (showcase / live). */
  funnel?: StudioSessionFunnel;
  last_job_status?: string;
  photos_ingested?: number;
  photos_analyzed?: number;
  source?: string;
};

export type StudioRecentDelivery = {
  session_key: string;
  session_date: string;
  /** Session intake (previews / ingested). Optional for older API payloads. */
  photos_imported?: number;
  photos_exported: number;
  previews_dir: string;
};

export type StudioSessionsResponse = {
  archive_root: string;
  active: {
    session_key: string;
    previews_dir: string;
    preview_count?: number;
    has_analysis_results?: boolean;
  } | null;
  sessions: StudioSessionRow[];
  count: number;
  recent_deliveries?: StudioRecentDelivery[];
};

export type StudioStatusResponse = {
  archive_root: string;
  active: StudioSessionsResponse["active"];
  session: {
    session_key: string;
    previews_dir: string;
    preview_count: number;
    has_analysis_results: boolean;
    activity: "idle" | "running" | "analyzed" | "failed";
    brain_session_id: number | null;
  } | null;
  job: {
    id: number;
    job_type: string;
    status: string;
    trace_id: string;
    elapsed_sec: number | null;
    is_running: boolean;
  } | null;
  pipeline: {
    labels: string[];
    current_index: number;
    complete: boolean;
    failed: boolean;
    stages?: Array<{
      label: string;
      state: "done" | "active" | "pending" | "failed";
      count_in: number | null;
      count_out: number | null;
      duration_sec: number | null;
    }>;
    workflow_stages?: Array<{
      label: string;
      count: number | null;
      state: "done" | "active" | "pending" | "failed";
    }>;
  };
  events: Array<{
    id: number;
    to_status: string | null;
    message: string | null;
    created_at: number | null;
    payload_json?: unknown;
  }>;
};

export type StudioLifetimeStats = {
  sessions_total: number;
  photos_total: number;
  exported_photos_total?: number;
  avg_processing_sec?: number | null;
  auto_reject_rate_pct?: number | null;
  average_keep_rate_pct?: number | null;
  total_runtime_sec?: number | null;
  total_runtime_hours?: number | null;
  /** @deprecated use auto_reject_rate_pct */
  auto_filter_rate_pct?: number | null;
  source?: string;
};

const STUDIO_SESSIONS_LIST_LIMIT = 500;

export async function fetchStudioSessions(): Promise<StudioSessionsResponse> {
  const q = new URLSearchParams({ limit: String(STUDIO_SESSIONS_LIST_LIMIT) });
  const res = await fetch(`${API_BASE}/api/studio/sessions?${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`studio sessions ${res.status}`);
  return res.json();
}

export type StudioInfraOverview = {
  workers_online: number;
  workers_total: number;
  queue_depth: number;
  pipeline_active: number;
  jobs_processed: number;
  average_latency_ms: number | null;
  pipeline_success_rate_pct: number | null;
  redis_status: string;
  database_status: string;
};

export async function fetchStudioLifetimeStats(): Promise<StudioLifetimeStats> {
  const res = await fetch(`${API_BASE}/api/landing/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`studio stats ${res.status}`);
  return res.json();
}

export async function fetchStudioInfraOverview(): Promise<StudioInfraOverview> {
  const res = await fetch(`${API_BASE}/api/studio/infra-overview`, { cache: "no-store" });
  if (!res.ok) throw new Error(`studio infra overview ${res.status}`);
  return res.json();
}

export async function fetchStudioStatus(previewsDir: string): Promise<StudioStatusResponse> {
  const q = new URLSearchParams({ previews_dir: previewsDir });
  const res = await fetch(`${API_BASE}/api/studio/status?${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`studio status ${res.status}`);
  return res.json();
}

export type StudioFeaturedFrame = {
  path_quoted: string;
  file?: string | null;
  highlight: string;
  score_label: string;
  score_value: number;
  score_display: string;
};

export type StudioFeaturedFramesResponse = {
  previews_dir: string;
  frames: StudioFeaturedFrame[];
  count: number;
};

export async function fetchStudioFeaturedFrames(
  previewsDir: string,
): Promise<StudioFeaturedFramesResponse> {
  const q = new URLSearchParams({ previews_dir: previewsDir });
  const res = await fetch(`${API_BASE}/api/studio/featured-frames?${q}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`studio featured frames ${res.status}`);
  return res.json();
}

export async function setActiveSession(previewsDir: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/studio/active-session`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ previews_dir: previewsDir }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `active-session ${res.status}`);
  }
}

export async function startStudioAnalyze(
  previewsDir: string,
  opts?: { forceFullRerun?: boolean },
): Promise<{ job_id: number; status: string; force_full_rerun?: boolean }> {
  const res = await fetch(`${API_BASE}/api/studio/analyze`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      previews_dir: previewsDir,
      force_full_rerun: opts?.forceFullRerun ?? true,
    }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `analyze ${res.status}`);
  }
  return res.json();
}

export function shortenPath(p: string, max = 52): string {
  if (p.length <= max) return p;
  const head = Math.floor(max * 0.35);
  const tail = max - head - 1;
  return `${p.slice(0, head)}…${p.slice(-tail)}`;
}

export type StudioIngestConfig = {
  ingest_monitor_path: string;
  archive_root: string;
  session_folder_name: string;
  session_folder_name_auto: boolean;
  updated_at: number | null;
  config_path?: string;
};

export async function fetchIngestConfig(): Promise<StudioIngestConfig> {
  const res = await fetch(`${API_BASE}/api/studio/ingest-config`, { cache: "no-store" });
  if (!res.ok) throw new Error(`ingest-config ${res.status}`);
  return res.json();
}

export async function saveIngestConfig(body: {
  ingest_monitor_path?: string;
  archive_root?: string;
  session_folder_name?: string;
}): Promise<StudioIngestConfig> {
  const res = await fetch(`${API_BASE}/api/studio/ingest-config`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(typeof data.detail === "string" ? data.detail : `ingest-config save ${res.status}`);
  }
  return res.json();
}
