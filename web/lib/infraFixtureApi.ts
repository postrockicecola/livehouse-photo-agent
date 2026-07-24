/**
 * Shared fixture + proxy helpers for /api/infra/* showcase drill-downs.
 * Explicit job routes and the catch-all both use this so portfolio deploys
 * never 404 on walkthrough jobs (#61 / #62) when FastAPI is absent.
 */
import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isLandingOnly, isShowcase, loadFixture, type FixtureName } from "@/lib/dataSource";

export const SHOWCASE_SUCCESS_JOB_ID = "61";
export const SHOWCASE_FALLBACK_JOB_ID = "62";

export function isReadOnlyInfraDeploy(): boolean {
  return isShowcase() || isLandingOnly();
}

export function jobDetailFixtureName(jobId: string): FixtureName {
  return jobId === SHOWCASE_FALLBACK_JOB_ID ? "infra-job-detail-fallback" : "infra-job-detail";
}

export function jobTimelineFixtureName(jobId: string): FixtureName {
  // Success job has a full timeline snapshot; fallback is adapted from detail below.
  return jobId === SHOWCASE_FALLBACK_JOB_ID ? "infra-job-detail-fallback" : "infra-job-timeline";
}

type LooseRecord = Record<string, unknown>;

/** Build a JobTimeline-shaped payload from a job-detail fixture (used for #62). */
export function detailToTimelinePayload(detail: LooseRecord): LooseRecord {
  if (detail && typeof detail === "object" && "time_window" in detail && "spans" in detail) {
    return detail;
  }
  const job = (detail.job && typeof detail.job === "object" ? detail.job : {}) as LooseRecord;
  const events = Array.isArray(detail.events) ? (detail.events as LooseRecord[]) : [];
  const modelRuns = Array.isArray(detail.model_runs) ? (detail.model_runs as LooseRecord[]) : [];
  const artifacts = Array.isArray(detail.artifacts) ? (detail.artifacts as LooseRecord[]) : [];

  const eventTs = events
    .map((e) => Number(e.created_at))
    .filter((n) => Number.isFinite(n));
  const jobStart = Number(job.enqueued_at ?? job.created_at ?? eventTs[0] ?? 0);
  const jobEnd = Number(job.finished_at ?? job.updated_at ?? eventTs[eventTs.length - 1] ?? jobStart + 1);
  const t0 = eventTs.length ? Math.min(jobStart, ...eventTs) : jobStart;
  const t1 = Math.max(t0 + 1, eventTs.length ? Math.max(jobEnd, ...eventTs) : jobEnd);

  const spans: LooseRecord[] = [];
  for (const ev of events) {
    const ts = Number(ev.created_at);
    if (!Number.isFinite(ts)) continue;
    spans.push({
      id: `ev-${ev.id ?? ts}`,
      kind: "job_event",
      ts,
      label: `${ev.from_status ?? "∅"} → ${ev.to_status ?? "?"}`,
      from_status: ev.from_status ?? null,
      to_status: ev.to_status ?? null,
      meta: { message: ev.message ?? null },
    });
  }
  for (const mr of modelRuns) {
    const latency = Number(mr.latency_ms);
    const endTs = Number.isFinite(latency) ? t1 : t1;
    const startTs = Number.isFinite(latency) ? Math.max(t0, endTs - Math.round(latency / 1000)) : t0;
    spans.push({
      id: `mr-${mr.id ?? mr.provider ?? startTs}`,
      kind: "model_run",
      ts: startTs,
      label: `${mr.provider ?? "model"} / ${mr.model_name ?? "?"} · ${mr.status ?? "?"}`,
      duration_ms: Number.isFinite(latency) ? latency : null,
      meta: {
        status: mr.status ?? null,
        error_type: mr.error_type ?? null,
        fallback_provider: mr.fallback_provider ?? null,
        attempt: mr.attempt ?? null,
      },
    });
  }
  for (const art of artifacts) {
    const ts = Number(art.generated_at ?? art.created_at ?? t1);
    spans.push({
      id: `art-${art.artifact_id ?? art.path ?? ts}`,
      kind: "artifact",
      ts: Number.isFinite(ts) ? ts : t1,
      label: String(art.kind ?? "artifact"),
      meta: { path: art.path ?? null, is_primary: art.is_primary ?? false },
    });
  }
  spans.sort((a, b) => Number(a.ts) - Number(b.ts));

  const jid = Number(job.id);
  const traceId = (job.trace_id as string | null | undefined) ?? null;

  return {
    ...detail,
    trace_id: traceId,
    related_job_ids: Number.isFinite(jid) ? [jid] : [],
    anchor_job_id: Number.isFinite(jid) ? jid : undefined,
    job_ids: Number.isFinite(jid) ? [jid] : [],
    worker: null,
    context: { showcase: true, degraded: Boolean(job.degraded ?? job.fallback_used) },
    spans,
    time_window: { t0, t1, width_seconds: Math.max(1, t1 - t0) },
    job_relationships: {
      root_job_id: Number.isFinite(jid) ? jid : undefined,
      parent_job_id: null,
      depends_on_job_id: null,
      child_job_ids: [],
      dependent_job_ids: [],
      is_root_of_group: true,
    },
    job_graph: {
      scope: "single_job",
      root_job_id: Number.isFinite(jid) ? jid : undefined,
      anchor_job_id: Number.isFinite(jid) ? jid : undefined,
      nodes: Number.isFinite(jid)
        ? [
            {
              job_id: jid,
              job_type: job.job_type ?? null,
              status: job.status ?? null,
              total_latency_ms: job.total_latency_ms ?? null,
              on_critical_path: true,
            },
          ]
        : [],
      edges: [],
    },
    agent: null,
  };
}

export function fixtureForInfraPath(segments: string[]): FixtureName | null {
  const p = segments.join("/");
  const exact: Record<string, FixtureName> = {
    metrics: "infra-metrics",
    "metrics/history": "infra-metrics-history",
    workers: "infra-workers",
    providers: "infra-providers",
    cost: "infra-cost",
    "dead-letter": "infra-dead-letter",
    "runtime-stream": "infra-runtime-stream",
    brain: "infra-brain",
    "agent/runs": "infra-agent-runs",
    jobs: "infra-jobs",
  };
  if (p in exact) return exact[p];
  const timeline = /^jobs\/([^/]+)\/timeline$/.exec(p);
  if (timeline) return jobTimelineFixtureName(timeline[1]);
  if (/^jobs\/[^/]+\/stages$/.test(p)) return "infra-job-stages";
  const job = /^jobs\/([^/]+)$/.exec(p);
  if (job) return jobDetailFixtureName(job[1]);
  if (/^traces\/[^/]+$/.test(p)) return "infra-trace";
  return null;
}

export function loadInfraFixturePayload(segments: string[]): unknown | null {
  const name = fixtureForInfraPath(segments);
  if (!name) return null;
  const raw = loadFixture<LooseRecord>(name);
  const timeline = /^jobs\/([^/]+)\/timeline$/.exec(segments.join("/"));
  if (timeline) return detailToTimelinePayload(raw);
  return raw;
}

export function infraFixtureResponse(segments: string[]): NextResponse | null {
  const payload = loadInfraFixturePayload(segments);
  if (payload == null) return null;
  return NextResponse.json(payload);
}

export async function proxyInfraBackend(req: NextRequest, segments: string[]): Promise<NextResponse> {
  const search = req.nextUrl.search;
  const url = `${galleryApiOrigin()}/api/infra/${segments.join("/")}${search}`;
  const init: RequestInit = { method: req.method, cache: "no-store" };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
    init.headers = { "content-type": req.headers.get("content-type") ?? "application/json" };
  }
  try {
    const res = await fetch(url, init);
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: { "content-type": res.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "infra backend unavailable" }, { status: 502 });
  }
}

/** Read-only deploy → fixture; full mode → proxy with fixture fallback on 404/5xx. */
export async function serveInfraGet(req: NextRequest, segments: string[]): Promise<NextResponse> {
  if (isReadOnlyInfraDeploy()) {
    return (
      infraFixtureResponse(segments) ??
      NextResponse.json({ detail: "not available in read-only showcase" }, { status: 404 })
    );
  }
  const proxied = await proxyInfraBackend(req, segments);
  if (proxied.status >= 500 || proxied.status === 404) {
    const fallback = infraFixtureResponse(segments);
    if (fallback) return fallback;
  }
  return proxied;
}
