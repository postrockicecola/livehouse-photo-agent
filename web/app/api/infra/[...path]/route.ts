import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isLandingOnly, isShowcase, loadFixture, type FixtureName } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

/**
 * Catch-all for the /infra console's ~14 FastAPI endpoints. These have no other
 * Next handler — locally they proxy to FastAPI (this handler wins over the
 * next.config rewrite, so we proxy explicitly); on Vercel (showcase / landing-only)
 * there is no backend, so we serve committed snapshots. Parameterized drill-downs
 * (jobs/{id}, traces/{id}) collapse to one representative snapshot.
 *
 * Resilience: LANDING_ONLY or SHOWCASE_MODE always serve fixtures. In full mode,
 * proxy failures fall back to the same snapshots (same pattern as /api/landing/*).
 */
/** Showcase job drill-downs: #62 = fallback recovery; others → success #61 snapshot. */
function jobDetailFixture(jobId: string): FixtureName {
  return jobId === "62" ? "infra-job-detail-fallback" : "infra-job-detail";
}

function fixtureForPath(segments: string[]): FixtureName | null {
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
  if (timeline) {
    // Fallback case ships events inside the detail fixture; success keeps timeline snapshot.
    return timeline[1] === "62" ? "infra-job-detail-fallback" : "infra-job-timeline";
  }
  if (/^jobs\/[^/]+\/stages$/.test(p)) return "infra-job-stages";
  const job = /^jobs\/([^/]+)$/.exec(p);
  if (job) return jobDetailFixture(job[1]);
  if (/^traces\/[^/]+$/.test(p)) return "infra-trace";
  return null;
}

/** True when this deploy has no FastAPI (Vercel / EdgeOne portfolio). */
function isReadOnlyDeploy(): boolean {
  return isShowcase() || isLandingOnly();
}

function fixtureResponse(segments: string[]): NextResponse | null {
  const name = fixtureForPath(segments);
  if (!name) return null;
  return NextResponse.json(loadFixture(name));
}

async function proxyToBackend(req: NextRequest, segments: string[]): Promise<NextResponse> {
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

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const segments = params.path ?? [];

  // Portfolio deploys: never probe localhost FastAPI.
  if (isReadOnlyDeploy()) {
    return (
      fixtureResponse(segments) ??
      NextResponse.json({ detail: "not available in read-only showcase" }, { status: 404 })
    );
  }

  const proxied = await proxyToBackend(req, segments);
  // Full mode: keep last-known showcase snapshot when the live API is down.
  if (proxied.status >= 500) {
    const fallback = fixtureResponse(segments);
    if (fallback) return fallback;
  }
  return proxied;
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (isReadOnlyDeploy()) {
    return NextResponse.json(
      { detail: "只读演示模式：Vercel 快照不支持重试/取消/暂停等写操作" },
      { status: 403 },
    );
  }
  return proxyToBackend(req, params.path ?? []);
}
