import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";

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
  try {
    const data = await runStudioCli<StudioInfraOverviewResponse>("infra-overview");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({
      workers_online: 0,
      workers_total: 0,
      queue_depth: 0,
      pipeline_active: 0,
      jobs_processed: 0,
      average_latency_ms: null,
      pipeline_success_rate_pct: null,
      redis_status: "unknown",
      database_status: "unknown",
    } satisfies StudioInfraOverviewResponse);
  }
}
