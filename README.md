# Livehouse Photography Agent

![CI](https://github.com/postrockicecola/livehouse-photo-agent/actions/workflows/ci.yml/badge.svg)

> A **job-centric AI runtime** for vision workflows: durable job state machines for VLM inference, with backpressure, fallback, a run ledger, and an operator console so work is recoverable, observable, and evaluable.

Livehouse photography is the primary workload: a real batch vision pipeline, not a thin wrapper around a single model API. The focus is durable orchestration — expensive, flaky VLM calls run inside a recoverable, observable, evaluable job runtime.

```text
Photo ingest
  → cheap vision gates
  → durable job system
  → bounded VLM inference
  → model / run ledger
  → Gallery / Infra Console
```

---

## Overview

Livehouse Photography Agent processes a concert photo session end to end: cheap vision gates filter frames, durable jobs orchestrate bounded VLM inference, and the web UI exposes curation, export, and operational visibility.

| Input | Output |
|------|--------|
| A session of similar Livehouse preview frames | Filtered keepers, scores, bilingual commentary, near-duplicate removal, Gallery export |

### Web surfaces

- **Studio** — submit sessions and track processing status
- **Infra Console** (`/infra`) — jobs, timelines, model attempts, workers, and cost
- **Gallery** — review results, confirm selections, and export

### Reference run

The repository ships a recorded session snapshot for local demos and the read-only showcase deploy:

| Metric | Value | Source |
|--------|------:|--------|
| Photos in | 412 | Showcase fixture |
| VLM calls (ledger) | 288 | `web/fixtures/infra-metrics.json` |
| Keep rate | 79% | `landing-stats.average_keep_rate_pct` |
| End-to-end (job #61) | ~12.5 min | `total_latency_ms` on `ANALYZE_SESSION` |

Try the guided Infra Console tour at [`/infra?tour=1`](http://127.0.0.1:3000/infra?tour=1).  
Optional walkthrough video: `web/public/demo/walkthrough.mp4`.

### Data provenance labels

Every metric and chart should carry one of:

| Label | Meaning |
|------|---------|
| **Live** | From the currently running local system |
| **Recorded Run** | Real, reproducible historical run / archive snapshot |
| **Simulated** | Shape demo or injected latency — not a production measurement |
| **Showcase Fixture** | Committed static snapshot for the read-only deploy |

Do not present Simulated or Showcase Fixture numbers as live production SLOs.

---

## Design goals

The main path is built around four properties:

- **Durable jobs** — claim, retry, and dead-letter state live in SQL; Celery `AsyncResult` is not authoritative
- **Bounded inference** — prioritized queue with concurrency and backpressure limits
- **Model fallback** — primary → fallback routing with an attempt ledger
- **End-to-end observability** — `job_events`, `model_runs` / attempts, and Infra Console drill-down

```mermaid
flowchart LR
    A["Photo ingest"] --> B["Cheap vision + fast score gates"]
    B --> C["Durable jobs SSOT"]
    C --> D["Bounded VLM"]
    D --> E["Results + run ledger"]
    E --> F["Gallery + Infra Console"]
```

### Linear main path

```text
ingest (Go SD/brain → sessions/photos)
  → POST /api/ingest/check_new_images
  → tasks.process_brain_ingested          # seed ANALYZE_SESSION jobs
  → tasks.run_job(job_id)
  → services.job_executor.JobExecutor     # atomic claim → run → finalize
  → PipelineStageRunner                   # Stage1 OpenCV → Stage2 fast → Stage3 VLM
  → PrioritizedInferenceQueue + router    # primary → fallback
  → artifacts (analysis_results.json, job_events, model_runs)
```

Celery carries only a `job_id`. SQLite (`jobs`, `job_events`, `workers`, model-run ledger) is the execution source of truth.

| Layer | Meaning | Start here |
|------|---------|------------|
| **Main path** | Jobs + events as SSOT; executor claims by `job_id` | `tasks/run_job.py` → `services/job_executor.py` → `services/processor/pipeline_stage_runner.py` |
| **Dispatch & ingest** | Seed `ANALYZE_SESSION` and dispatch by id | `tasks/ingest.py`, `services/scheduler/` |
| **HTTP surface** | Gallery + tasks + infra | `gallery_server.py`, `api/` |
| **Infra UI** | Operator console over the same APIs | `web/app/infra/page.tsx` |
| **Optional inference swap** | `model.use_inference_layer: true` → `inference/` | `configs/livehouse.yaml` |

---

## Job walkthrough

The Infra Console includes a **Guided Tour** (`/infra?tour=1`) with two example jobs from the reference fixtures:

### Success — job `#61` (default expanded)

```text
QUEUED → CLAIMED → PREPROCESSING → INFERENCING → SUCCEEDED
```

Drill-down: event timeline → provider calls (`model_runs`) → artifacts.

### Fallback recovery — job `#62`

```text
primary TIMEOUT → fallback provider SUCCEEDED → job SUCCEEDED (degraded)
```

Fixture: `web/fixtures/infra-job-detail-fallback.json` (two `model_run` attempts). The read-only showcase serves job `#62` from that snapshot; other job IDs use the success detail fixture.

---

## Evaluation

UI: **`/eval`** (also summarized on the landing `#evaluation` section).  
Report index: [`reports/eval/meta.json`](reports/eval/meta.json).

Fixed **250-image** human-labeled set (`data/eval/`). Config for full scoring: `configs/eval_stage3.yaml` (temp=0).  
Eval reports stamp a `protocol` block (seed, config hash, hardware, git sha) via `scripts/eval/protocol.py`.

| Strategy | Quality | VLM calls | Latency | Cost | Source |
|------|------|----------|------|------|-------------|
| Full VLM (eval Stage3) | Spearman **0.36** · MAE **6.09** · P@20 **0.55** | 100% of 250 | — | — | Recorded · `reports/eval/stage3_v6_qwen2vl_temp0.json` |
| Two-stage gating (production path) | admitted Spearman **0.52** · MAE **4.48** · keeper coverage **0.06** | **6.4%** of 250 (16 calls) | — | ~15× fewer VLM calls | Recorded · offline replay · `reports/eval/two_stage_gating.json` |
| Agent curation (budget 40) | **stratified** sel. P **0.43** · keeper recall **0.20** | 40/250 = **16%** | — | lower VLM count by design | Recorded · `reports/eval/agent_selection.json` |

**Agent vs baselines (n=250, budget=40, select 30):**

| Arm | Selection precision | Keeper recall | P@10 |
|-----|--------------------:|--------------:|-----:|
| random | 0.40 | 0.205 | 0.40 |
| heuristic (greedy fast_score) | 0.30 | 0.157 | 0.40 |
| **stratified (default)** | **0.43** | **0.205** | **0.50** |
| oracle | 0.93 | 0.458 | 0.80 |

**LLM arm (n=60 subset):** selection precision heuristic 0.33 vs llm 0.36; `llm_decision_rate≈0.06` with heavy heuristic fallback (`reports/eval/agent_selection_llm.json`).  
**Notes:** stratified allocation beats greedy heuristic (and random on P@10 / sel. P) under the same budget; the LLM planner does not yet consistently beat stratified. Preference pairs for a future SFT/DPO loop: `data/eval/preferences/`.

**Simulated / illustrative only** (see [Data provenance labels](#data-provenance-labels)):

- `reports/eval/quant_compare_example.json` — **Simulated**
- `scripts/gpu_pressure_demo.py --simulate`, loadtest simulate artifacts — **Simulated**

```bash
python scripts/eval/sample_eval_set.py --session "<archive>/<YYYY-MM-DD>" \
    --target 250 --out data/eval/images --manifest data/eval/manifest.json
python run_pipeline.py --config configs/eval_stage3.yaml --source-dir data/eval/images --no-serve --no-checkpoint
python scripts/label_server.py --images data/eval/images --labels data/eval/labels.jsonl
python scripts/eval_stage3.py run --labels data/eval/labels.jsonl \
    --predictions data/eval/images/analysis_results.json
# Production gating vs full-VLM (offline replay, no GPU):
python scripts/eval/eval_two_stage_gating.py --out reports/eval/two_stage_gating.json
# Planner arms (includes stratified default):
python scripts/eval/eval_agent_selection.py --labels data/eval/labels.jsonl \
    --predictions reports/eval/baseline_v4_stage1_two_merged_predictions.json \
    --features data/eval/_temp0_run/.luma_pipeline_staged/eligible_after_stage2.jsonl \
    --budget 40 --out reports/eval/agent_selection.json
# Preference pairs for a future SFT/DPO loop:
python scripts/eval/export_preferences.py --labels data/eval/labels.jsonl
```

---

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

1. Edit `configs/livehouse.yaml` (`paths.source_dir`, `model.*`, optional `model.use_inference_layer`).
2. Full stack: `./start_all.sh` (or `./deploy/up.sh up --build`).
3. Pipeline-only (no jobs / Infra timeline):  
   `python run_pipeline.py --config configs/livehouse.yaml --source-dir "<previews>" --no-serve`
4. URLs: Next.js <http://127.0.0.1:3000> · FastAPI <http://127.0.0.1:8080> · Infra `/infra`

Copy `web/.env.example` → `web/.env.local` if the API host/port differs.

**Requirements:** Python 3.10+, Redis, Node 18+, Ollama (or compatible VLM HTTP API). Optional: Go ingest, exiftool, macOS `powermetrics` for GPU telemetry.

---

## Scope and limitations

This project targets a **single-node AI runtime**. It is not designed as a multi-tenant distributed platform:

1. **SQLite** holds execution state (suitable for one machine; not a cluster database).
2. **In-process inference admission** and a bounded queue (not cluster-wide quotas).
3. **Single-node storage** with local archive paths for artifacts.

**Platform hooks (in progress):** portable brain backend selector (`LIVEHOUSE_BRAIN_BACKEND`), artifact `content_digest`, optional per-scope VLM hour quota, optional OTEL bootstrap. See [`docs/PLATFORM_SCOPE.txt`](docs/PLATFORM_SCOPE.txt).

---

## Configuration

| Area | Where |
|------|------|
| Paths, thresholds, VLM / inference toggle | `configs/livehouse.yaml` |
| Celery broker / backend | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` |
| Worker pool label | `LIVEHOUSE_EXECUTOR_CLASS` (default `general`) |
| GPU telemetry path | `LUMA_GPU_TELEMETRY_PATH` |
| Brain DB / archive | `LUMA_BRAIN_DB`, `LUMA_ARCHIVE_ROOT` (see `.env.example`) |

Secrets stay in `.env` (git-ignored).

---

## Project layout (short)

```text
configs/        # livehouse.yaml and friends
tasks/          # run_job, ingest, maintenance
services/       # job_executor, processor/, scheduler/, agent/
engine/         # LivehouseVLM; operators
inference/      # optional router, providers, ledger
infra/          # WorkerManager, metrics, gpu_telemetry
api/            # gallery_routes, infra_routes
gallery_server.py
web/            # Next.js — Studio · Gallery · Infra Console
scripts/        # eval harness, GPU / scaling demos
data/eval/      # fixed labels + manifest
reports/eval/   # Recorded Run JSON reports
```

Deeper design notes: [`docs/PROJECT_GUIDE.txt`](docs/PROJECT_GUIDE.txt), [`docs/PLATFORM_SCOPE.txt`](docs/PLATFORM_SCOPE.txt).

---

## Editor conventions

See `.cursor/rules/*.mdc` and `AGENTS.md`.

## License

[MIT](LICENSE) © postrockicecola
