import { NextRequest, NextResponse } from "next/server";
import { infraFixtureResponse } from "@/lib/infraFixtureApi";

export const dynamic = "force-dynamic";

/**
 * Portfolio-only infra reads. Always returns committed fixtures — no FastAPI,
 * no SHOWCASE_MODE gate. Used by job/trace drill-downs when `/api/infra/*`
 * is unavailable or still proxies to a missing backend job.
 */
export async function GET(_req: NextRequest, { params }: { params: { path: string[] } }) {
  return (
    infraFixtureResponse(params.path ?? []) ??
    NextResponse.json({ detail: "showcase fixture not found" }, { status: 404 })
  );
}
