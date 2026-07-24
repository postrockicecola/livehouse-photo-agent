/** Shared infra worker / Celery broker types (UI lives in WorkerPoolPanel). */

export type CeleryBrokerStatus = {
  online?: boolean;
  celery_hostname?: string;
  active_count?: number;
  reserved_count?: number;
  scheduled_count?: number;
  active_tasks?: Array<{ name?: string; id?: string; args_preview?: string | null }>;
  pool_max_concurrency?: number | null;
};

export type InfraWorkerRow = {
  id?: number;
  worker_name?: string;
  worker_type?: string;
  status?: string;
  capacity?: number | null;
  inflight?: number | null;
  last_heartbeat?: number | null;
  celery_broker?: CeleryBrokerStatus;
};
