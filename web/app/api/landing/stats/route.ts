import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export type LandingStatsResponse = {
  sessions_total: number;
  photos_total: number;
  exported_photos_total?: number;
  avg_processing_sec?: number | null;
  auto_reject_rate_pct?: number | null;
  average_keep_rate_pct?: number | null;
  total_runtime_sec?: number | null;
  total_runtime_hours?: number | null;
  auto_filter_rate_pct?: number | null;
  source?: string;
};

export async function GET() {
  // Showcase (Vercel): no Python/backend — serve the committed snapshot so the
  // landing page shows real project numbers instead of zeros.
  if (isShowcase()) {
    return NextResponse.json(loadFixture<LandingStatsResponse>("landing-stats"));
  }
  try {
    const data = await runStudioCli<LandingStatsResponse>("stats");
    return NextResponse.json(data);
  } catch {
    // Backend unreachable (e.g. fresh clone / deploy without SHOWCASE_MODE):
    // fall back to the snapshot rather than all-zero placeholders.
    return NextResponse.json(loadFixture<LandingStatsResponse>("landing-stats"));
  }
}
