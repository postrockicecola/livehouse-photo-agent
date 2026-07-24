import { NextRequest } from "next/server";
import { serveInfraGet } from "@/lib/infraFixtureApi";

export const dynamic = "force-dynamic";

/** Full timeline page (`/infra/jobs/{id}`) and JobTimeline client fetch. */
export async function GET(req: NextRequest, { params }: { params: { jobId: string } }) {
  return serveInfraGet(req, ["jobs", params.jobId, "timeline"]);
}
