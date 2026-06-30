export type RuntimeHealth = "HEALTHY" | "DEGRADED" | "CRITICAL" | "UNKNOWN";

export type BrainDashboardData = {
  db_path?: string;
  table_counts?: Record<string, number>;
  photos_by_status?: Record<string, number>;
  jobs_by_type?: Record<string, number>;
  sessions?: Array<Record<string, unknown>>;
  photos?: Array<Record<string, unknown>>;
  limits?: { sessions?: number; photos?: number };
};

export type RuntimeEvent = {
  id: number;
  job_id: number;
  from_status?: string | null;
  to_status?: string | null;
  created_at: number;
  message?: string | null;
  stage_name?: string | null;
  worker_id?: number | null;
  worker_name?: string | null;
};

export type StageFlowStat = {
  stage_key: string;
  status: string;
  count: number;
  avg_latency_ms?: number | null;
};

export type RuntimeStreamData = {
  events: RuntimeEvent[];
  stages: StageFlowStat[];
  retries_recent: RuntimeEvent[];
};

export type InfraMetricsSnapshot = {
  jobs?: {
    total?: number;
    by_status?: Record<string, number>;
  };
  queue_backlog?: {
    active?: number;
    reserved?: number;
    scheduled?: number;
    redis_list_len?: number | null;
    celery_unavailable?: boolean;
    workers?: number;
  };
  workers?: {
    total?: number;
    fresh_within_120s?: number;
    heartbeat_fresh_window_sec?: number;
    by_status?: Record<string, number>;
    pipeline_admission?: {
      headroom?: number;
      total_capacity?: number;
      total_inflight?: number;
      online_workers?: number;
    };
  };
  inference_latency?: {
    avg_ms?: number | null;
    last_ms?: number | null;
  };
  inference_queue?: {
    depth?: number;
    active_workers?: number;
    max_inflight?: number;
    avg_job_e2e_ms?: number | null;
    throughput_img_per_sec_30s?: number | null;
  };
  inference_from_database?: {
    model_runs_inflight_in_db?: number;
    by_provider?: Array<{
      provider: string;
      avg_provider_latency_ms?: number | null;
      succeeded_total?: number;
      failed_total?: number;
      terminal_total?: number;
    }>;
  };
  providers?: Array<{
    provider?: string;
    requests?: number;
    failures?: number;
    fallbacks?: number;
    avg_latency_ms?: number | null;
    last_latency_ms?: number | null;
  }>;
};

export type InfraWorkerRow = {
  id?: number;
  worker_name?: string;
  worker_type?: string;
  status?: string;
  capacity?: number | null;
  inflight?: number | null;
  last_heartbeat?: number | null;
};

export type InfraProviderRow = {
  name: string;
  display_name?: string;
  enabled: boolean;
  endpoint?: string | null;
  model_name?: string | null;
  fallback_model_name?: string | null;
  runtime?: {
    requests?: number;
    failures?: number;
    fallbacks?: number;
    avg_latency_ms?: number | null;
    last_latency_ms?: number | null;
  } | null;
};

export const PIPELINE_STAGES = [
  { key: "PREPARE_INPUT", label: "PREPARE" },
  { key: "STAGE1_FILTER", label: "STAGE1" },
  { key: "STAGE2_FAST_SCORE", label: "STAGE2" },
  { key: "STAGE3_VLM", label: "VLM" },
  { key: "WRITE_ARTIFACT", label: "EXPORT" },
  { key: "FINALIZE", label: "FINALIZE" },
] as const;

export const PIPELINE_ACTIVE = new Set(["CLAIMED", "PREPROCESSING", "INFERENCING", "POSTPROCESSING"]);
export const PIPELINE_FAILED = new Set([
  "FAILED_PERMANENT",
  "FAILED_RETRYABLE",
  "DEAD_LETTERED",
  "CANCELLED",
]);
