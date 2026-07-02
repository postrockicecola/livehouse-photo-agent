import { NextResponse } from "next/server";
import { LANDING_BRAIN_FALLBACK_COUNTS } from "@/lib/productIa";
import { runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

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
  // Showcase (Vercel): serve the committed snapshot (real brain counts + trace).
  if (isShowcase()) {
    return NextResponse.json(loadFixture<LandingBrainResponse>("landing-brain"));
  }
  try {
    const data = await runStudioCli<LandingBrainResponse>("landing-brain");
    return NextResponse.json(data);
  } catch {
    // Backend unreachable: prefer the snapshot over empty counts.
    return NextResponse.json(loadFixture<LandingBrainResponse>("landing-brain"));
  }
}
