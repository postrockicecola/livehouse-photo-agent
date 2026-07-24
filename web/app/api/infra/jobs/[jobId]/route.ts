import { NextRequest } from "next/server";
import { serveInfraGet } from "@/lib/infraFixtureApi";

export const dynamic = "force-dynamic";

/** Explicit job detail — preferred over catch-all for walkthrough expand (#61/#62). */
export async function GET(req: NextRequest, { params }: { params: { jobId: string } }) {
  return serveInfraGet(req, ["jobs", params.jobId]);
}
