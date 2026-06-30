import { NextResponse } from "next/server";
import { LANDING_INFRA_FALLBACK_METRICS } from "@/lib/productIa";
import { runStudioCli } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

export type LandingInfraFlowItem = {
  id: number;
  job_id: number | null;
  from_status: string;
  to_status: string;
  created_at: number | null;
};

export type LandingInfraResponse = {
  metrics: typeof LANDING_INFRA_FALLBACK_METRICS;
  flow: LandingInfraFlowItem[];
};

export async function GET() {
  try {
    const data = await runStudioCli<LandingInfraResponse>("landing-infra");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ metrics: LANDING_INFRA_FALLBACK_METRICS, flow: [] });
  }
}
