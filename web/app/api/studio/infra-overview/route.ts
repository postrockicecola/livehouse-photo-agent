import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export type StudioInfraOverviewResponse = {
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

export async function GET() {
  if (isShowcase()) {
    return NextResponse.json(loadFixture<StudioInfraOverviewResponse>("studio-infra-overview"));
  }
  try {
    const data = await runStudioCli<StudioInfraOverviewResponse>("infra-overview");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(loadFixture<StudioInfraOverviewResponse>("studio-infra-overview"));
  }
}
