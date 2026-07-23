-- Luma Brain: lightweight photo ledger (SQLite).
-- Future semantic / vector search: add a sidecar table (e.g. photo_embeddings) keyed by photos.id,
-- or store an external vector DB id in photos.vector_ref — keep this file as the source of truth for row shape.

PRAGMA foreign_keys = ON;

-- Archive "session" (one row per logical ingest batch, usually one calendar folder under archive_root).
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_key TEXT NOT NULL,
  session_dir TEXT NOT NULL,
  archive_root TEXT NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  raw_dir TEXT NOT NULL DEFAULT '',
  previews_dir TEXT NOT NULL DEFAULT '',
  photo_count INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER NOT NULL,
  closed_at INTEGER,
  notes TEXT,
  UNIQUE (session_key, device_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC);

-- Per-file ledger. file_hash is a fast fingerprint (filename + size + mtime), not full-content hash.
-- photos.status = ingest + outcome only (NOT execution): NEW = registered/copy pending; INGESTED = file on disk,
--   pipeline outcome not yet recorded; ANALYZED = analysis outcome recorded for this row.
-- ANALYZING remains in the CHECK constraint for legacy DB rows only — runnable work / claims live in jobs.status (+ job_events).
CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_hash TEXT NOT NULL UNIQUE,
  file_path TEXT NOT NULL,
  device_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL CHECK (status IN ('NEW', 'INGESTED', 'ANALYZING', 'ANALYZED')) DEFAULT 'NEW',
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER,
  -- Optional hook for vector / ANN index: point id, sqlite-vec row, or JSON blob path.
  vector_ref TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_status ON photos(status);
CREATE INDEX IF NOT EXISTS idx_photos_session_status ON photos(session_id, status);
CREATE INDEX IF NOT EXISTS idx_photos_device ON photos(device_id);

-- ---------------------------------------------------------------------------
-- AI Infra extension tables (compatible add-on; do not replace sessions/photos)
-- Platform scope (single deployment, many logical projects): ``namespace`` + ``project_key`` default to ``'default'``.
-- Jobs are the SSOT for scope; sessions/photos are not namespaced in v1. Operators filter APIs and optional
-- Celery dispatch via env (see ``docs/PLATFORM_SCOPE.txt``). This is labeling / tenancy prep, not RBAC.
-- ---------------------------------------------------------------------------
-- Canonical job lifecycle statuses for queue/worker orchestration.
-- NOTE: keep in sync with app-side enums.
--   QUEUED
--   CLAIMED
--   PREPROCESSING
--   INFERENCING
--   POSTPROCESSING
--   SUCCEEDED
--   FAILED_RETRYABLE
--   FAILED_PERMANENT
--   CANCELLED
--   DEAD_LETTERED

-- Worker control + runtime: only ONLINE accepts new claims; DRAINING finishes inflight only;
-- PAUSED/ERROR block new claims. Celery/heartbeat must not overwrite PAUSED/DRAINING/ERROR (see luma_brain).
CREATE TABLE IF NOT EXISTS workers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  -- Logical executor pool / class (``general``, ``inference``, ``ingest``, …). Celery remains transport;
  -- routing uses this column with ``services.worker_pools``. Legacy labels ``celery`` / ``generic`` act as omnivores.
  worker_type TEXT NOT NULL DEFAULT 'generic',
  status TEXT NOT NULL DEFAULT 'ONLINE'
    CHECK (status IN ('ONLINE', 'OFFLINE', 'DRAINING', 'PAUSED', 'ERROR')),
  last_heartbeat INTEGER,
  capacity INTEGER NOT NULL DEFAULT 1,
  inflight INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  updated_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workers_name ON workers(worker_name);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat DESC);

CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_type TEXT NOT NULL,
  session_id INTEGER REFERENCES sessions(id) ON DELETE SET NULL,
  photo_id INTEGER REFERENCES photos(id) ON DELETE SET NULL,
  status TEXT NOT NULL
    CHECK (status IN (
      'QUEUED',
      'CLAIMED',
      'PREPROCESSING',
      'INFERENCING',
      'POSTPROCESSING',
      'SUCCEEDED',
      'FAILED_RETRYABLE',
      'FAILED_PERMANENT',
      'CANCELLED',
      'DEAD_LETTERED'
    )) DEFAULT 'QUEUED',
  priority INTEGER NOT NULL DEFAULT 0,
  attempt INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  -- Incremented on claim and stuck-requeue; terminal writers must match (fencing).
  claim_generation INTEGER NOT NULL DEFAULT 0,
  worker_id INTEGER REFERENCES workers(id) ON DELETE SET NULL,
  provider TEXT,
  model_name TEXT,
  fallback_used INTEGER NOT NULL DEFAULT 0 CHECK (fallback_used IN (0, 1)),
  enqueued_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  claimed_at INTEGER,
  started_at INTEGER,
  finished_at INTEGER,
  queue_wait_ms INTEGER,
  preprocess_ms INTEGER,
  inference_ms INTEGER,
  postprocess_ms INTEGER,
  total_latency_ms INTEGER,
  error_code TEXT,
  error_message TEXT,
  trace_id TEXT,
  -- Executor hints + ANALYZE_PATH parameters (JSON). DB is SSOT for what to run; Celery only receives job_id.
  payload_json TEXT,
  -- Stage-aware jobs: linear DAG of ``PIPELINE_STAGE`` rows sharing one ``root_job_id`` (first stage id).
  -- Legacy monolithic jobs: is_stage=0, stage_name/depends/root NULL.
  root_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  parent_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  stage_name TEXT,
  stage_order INTEGER,
  depends_on_job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
  is_stage INTEGER NOT NULL DEFAULT 0 CHECK (is_stage IN (0, 1)),
  -- Platform scope (single deployment, many logical projects): both default to ``default`` for legacy rows.
  namespace TEXT NOT NULL DEFAULT 'default',
  project_key TEXT NOT NULL DEFAULT 'default',
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  updated_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_priority ON jobs(status, priority DESC, enqueued_at ASC);
CREATE INDEX IF NOT EXISTS idx_jobs_namespace_project ON jobs(namespace, project_key);
CREATE INDEX IF NOT EXISTS idx_jobs_session_status ON jobs(session_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_photo_status ON jobs(photo_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_worker_status ON jobs(worker_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_trace_id ON jobs(trace_id);
CREATE INDEX IF NOT EXISTS idx_jobs_type_status ON jobs(job_type, status);
CREATE INDEX IF NOT EXISTS idx_jobs_root_job ON jobs(root_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_depends_on ON jobs(depends_on_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_stage_root ON jobs(root_job_id, stage_order);

-- payload_json: optional context per transition. On to_status = SUCCEEDED, pipeline may store
--   artifacts: [{ "kind", "path", "generated_at", "taxonomy"?, "role"?, "category"?, "stage"?, "source"?, ... }],
--   primary_artifact, artifact_registry_version, plus worker/source_dir/trace.
--   taxonomy buckets: preview | analysis_results | gallery_html | (reserved: export_package, model_output).
--   See services/job_artifacts.py (KIND_*, TAXONOMY_*). Rows are mirrored into ``artifacts`` for indexed queries.
CREATE TABLE IF NOT EXISTS job_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  from_status TEXT,
  to_status TEXT,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  message TEXT,
  payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_events_job_created ON job_events(job_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_job_events_to_status ON job_events(to_status, created_at DESC);

-- Artifact registry: one row per output file/object; complements job_events.payload_json (audit JSON).
-- Queryable lineage: job_id, kind, stage, source, job_event_id → the SUCCEEDED job_events row that
--   carried this job's artifact list (same transaction as sync_job_artifacts_from_success_event).
-- metadata_json: JSON extras not stored as columns (e.g. taxonomy, role, category, forward-compatible keys).
--   is_primary:see services.job_artifacts.select_primary_artifact (analysis_results preferred).
CREATE TABLE IF NOT EXISTS artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  generated_at INTEGER NOT NULL,
  metadata_json TEXT,
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
  stage TEXT,
  source TEXT,
  job_event_id INTEGER REFERENCES job_events(id) ON DELETE SET NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  -- Optional SHA-256 of local file bytes (CAS-ready metadata; path remains source of truth).
  content_digest TEXT
);

CREATE INDEX IF NOT EXISTS idx_artifacts_job_kind ON artifacts(job_id, kind);
CREATE INDEX IF NOT EXISTS idx_artifacts_job_generated ON artifacts(job_id, generated_at ASC);
CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind);

-- Inference runtime ledger: one row per enqueued VLM/LLM call (optional ``job_id`` from pipeline).
-- ``latency_ms`` = end-to-end (router wall time), aligned with ``end_to_end_latency_ms``; ``provider_latency_ms`` = sum of provider hops.
CREATE TABLE IF NOT EXISTS model_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  provider TEXT,
  model_name TEXT,
  primary_provider TEXT,
  fallback_provider TEXT,
  primary_model TEXT,
  final_model TEXT,
  request_payload_hash TEXT,
  status TEXT NOT NULL
    CHECK (status IN ('QUEUED', 'STARTED', 'SUCCEEDED', 'FAILED', 'TIMEOUT', 'CANCELLED')) DEFAULT 'QUEUED',
  latency_ms INTEGER,
  end_to_end_latency_ms INTEGER,
  provider_latency_ms INTEGER,
  queue_wait_ms INTEGER,
  degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
  fallback_used INTEGER NOT NULL DEFAULT 0 CHECK (fallback_used IN (0, 1)),
  error_type TEXT,
  error_message TEXT,
  prompt_length INTEGER,
  response_length INTEGER,
  -- Token accounting (best-effort; populated when the provider reports usage, e.g. Ollama
  -- prompt_eval_count / eval_count). Enables per-request cost/throughput attribution; NULL when unknown.
  prompt_tokens INTEGER,
  completion_tokens INTEGER,
  total_tokens INTEGER,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  outcome_attribution TEXT
);

CREATE TABLE IF NOT EXISTS model_run_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_run_id INTEGER NOT NULL REFERENCES model_runs(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('primary', 'fallback')),
  provider_id TEXT NOT NULL,
  model_name TEXT,
  latency_ms INTEGER NOT NULL,
  ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
  error_type TEXT,
  error_message TEXT,
  primary_skipped INTEGER NOT NULL DEFAULT 0 CHECK (primary_skipped IN (0, 1)),
  created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  UNIQUE(model_run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_model_runs_job_created ON model_runs(job_id, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_model_runs_provider_model ON model_runs(provider, model_name);
CREATE INDEX IF NOT EXISTS idx_model_runs_status_created ON model_runs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_runs_payload_hash ON model_runs(request_payload_hash);
CREATE INDEX IF NOT EXISTS idx_model_runs_error_type ON model_runs(error_type);

CREATE INDEX IF NOT EXISTS idx_model_run_attempts_run_seq ON model_run_attempts(model_run_id, seq);

-- Best-effort multi-process runtime metrics (e.g. per-worker inference queue depth). Not SSOT for jobs.
CREATE TABLE IF NOT EXISTS infra_runtime_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  component TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  UNIQUE(source, component)
);

CREATE INDEX IF NOT EXISTS idx_infra_runtime_snapshots_component
  ON infra_runtime_snapshots(component, updated_at DESC);

-- ---------------------------------------------------------------------------
-- Visual embedding index (CLIP / multimodal retrieval).
-- One row per (photo, model); vector is a BLOB of float32 bytes (little-endian).
-- dim records the embedding dimension for future multi-model support.
-- photos.vector_ref is updated to the model_name on upsert as a cheap "has
-- embedding" flag without a JOIN.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS photo_embeddings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  photo_id INTEGER NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
  model_name TEXT NOT NULL,
  vector BLOB NOT NULL,
  dim INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
  UNIQUE(photo_id, model_name)
);

CREATE INDEX IF NOT EXISTS idx_photo_embeddings_model
  ON photo_embeddings(model_name, photo_id);

-- ---------------------------------------------------------------------------
-- RLHF: pairwise human preference votes for Bradley-Terry reward modelling.
-- Each vote records one human comparison: winner_path beat loser_path.
-- session_key optionally scopes votes to a single photography session.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rlhf_votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  winner_path TEXT NOT NULL,
  loser_path  TEXT NOT NULL,
  session_key TEXT,
  source      TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'auto' | 'import'
  voter_id    TEXT,
  created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
  CHECK (winner_path != loser_path)
);

CREATE INDEX IF NOT EXISTS idx_rlhf_votes_winner    ON rlhf_votes(winner_path);
CREATE INDEX IF NOT EXISTS idx_rlhf_votes_loser     ON rlhf_votes(loser_path);
CREATE INDEX IF NOT EXISTS idx_rlhf_votes_session   ON rlhf_votes(session_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rlhf_votes_created   ON rlhf_votes(created_at DESC);

-- ---------------------------------------------------------------------------
-- Prompt A/B experiment framework.
-- prompt_variants: registry of named prompt templates with a free-form config blob.
-- prompt_experiment_runs: links a model_run to the variant that produced it,
-- plus the VLM score so we can compare score distributions across variants.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prompt_variants (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  description TEXT,
  prompt_text TEXT NOT NULL,
  variant_tag TEXT NOT NULL DEFAULT 'control',  -- 'control' | 'treatment_*'
  config_json TEXT,                             -- arbitrary extra params (JSON)
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at  INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS prompt_experiment_runs (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  model_run_id     INTEGER REFERENCES model_runs(id) ON DELETE CASCADE,
  variant_id       INTEGER NOT NULL REFERENCES prompt_variants(id),
  experiment_name  TEXT NOT NULL DEFAULT 'default',
  image_path       TEXT,
  vlm_score        REAL,
  outcome          TEXT,
  prompt_tokens    INTEGER,
  completion_tokens INTEGER,
  latency_ms       INTEGER,
  created_at       INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE INDEX IF NOT EXISTS idx_prompt_exp_runs_variant
  ON prompt_experiment_runs(variant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_prompt_exp_runs_experiment
  ON prompt_experiment_runs(experiment_name, variant_id);
