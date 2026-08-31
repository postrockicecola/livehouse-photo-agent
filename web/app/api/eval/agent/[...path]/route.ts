import { NextRequest, NextResponse } from "next/server";
import agentEvalLatest from "@/fixtures/agent-eval-latest.json";
import type { AgentEvalHistoryItem, AgentEvalReport } from "@/lib/agentEval";
import { isLandingOnly, isShowcase } from "@/lib/dataSource";
import { galleryApiOrigin } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

const latest = agentEvalLatest as AgentEvalReport;

function historyItem(report: AgentEvalReport): AgentEvalHistoryItem {
  return {
    run_id: report.run.run_id,
    started_at: report.run.started_at,
    completed_at: report.run.completed_at,
    passed: report.passed,
    overall_score: report.score.overall_score,
    behavior_success_rate: report.metrics.behavior?.success_rate,
    intent_accuracy: report.metrics.behavior?.intent_accuracy,
    quality_score: report.metrics.quality?.quality_score,
    trajectory_score: report.metrics.trajectory?.trajectory_score,
    p95_latency_ms: report.metrics.runtime?.p95_latency_ms,
    average_tokens: report.metrics.runtime?.average_tokens,
    model: report.run.model,
    case_count: report.run.case_count,
    threshold_failure_count: report.threshold_failures.length,
    regression_count: report.regression?.regressions?.length ?? 0,
  };
}

function fixtureResponse(req: NextRequest, path: string[]): NextResponse | null {
  const resource = path.join("/");
  if (resource === "latest" || resource === `runs/${latest.run.run_id}`) {
    const includeCases = req.nextUrl.searchParams.get("include_cases") !== "false";
    return NextResponse.json(includeCases ? latest : { ...latest, cases: undefined });
  }
  if (resource === "runs") {
    return NextResponse.json({
      schema_version: "agent_evaluation_index.v1",
      count: 1,
      runs: [historyItem(latest)],
    });
  }
  return null;
}

async function proxyBackend(req: NextRequest, path: string[]): Promise<NextResponse> {
  try {
    const response = await fetch(
      `${galleryApiOrigin()}/api/eval/agent/${path.join("/")}${req.nextUrl.search}`,
      { cache: "no-store" },
    );
    return new NextResponse(await response.text(), {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return NextResponse.json({ detail: "Agent evaluation backend unavailable" }, { status: 502 });
  }
}

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  const path = params.path ?? [];
  if (isShowcase() || isLandingOnly()) {
    return fixtureResponse(req, path) ?? NextResponse.json({ detail: "Agent evaluation report not found" }, { status: 404 });
  }

  const response = await proxyBackend(req, path);
  if (response.status === 404 || response.status >= 500) {
    return fixtureResponse(req, path) ?? response;
  }
  return response;
}
