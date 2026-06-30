import { NextResponse } from "next/server";
import { LANDING_BRAIN_FALLBACK_COUNTS } from "@/lib/productIa";
import { runStudioCli } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

export type LandingBrainTraceItem = {
  id: number;
  job_id: number | null;
  from_status: string;
  to_status: string;
  created_at: number | null;
};

export type LandingBrainResponse = {
  counts: typeof LANDING_BRAIN_FALLBACK_COUNTS;
  trace: LandingBrainTraceItem[];
};

export async function GET() {
  try {
    const data = await runStudioCli<LandingBrainResponse>("landing-brain");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({ counts: LANDING_BRAIN_FALLBACK_COUNTS, trace: [] });
  }
}
