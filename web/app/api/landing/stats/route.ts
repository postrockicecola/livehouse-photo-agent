import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";

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
  try {
    const data = await runStudioCli<LandingStatsResponse>("stats");
    return NextResponse.json(data);
  } catch {
    return NextResponse.json({
      sessions_total: 0,
      photos_total: 0,
      exported_photos_total: 0,
      avg_processing_sec: null,
      auto_reject_rate_pct: null,
      average_keep_rate_pct: null,
      total_runtime_sec: null,
      total_runtime_hours: null,
      auto_filter_rate_pct: null,
      source: "unavailable",
    });
  }
}
