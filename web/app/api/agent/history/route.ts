import { NextRequest, NextResponse } from "next/server";
import { isReadOnlyAgentDeploy, proxyAgentApi } from "@/lib/agentApiProxy";

export const dynamic = "force-dynamic";

/** Showcase has no durable agent memory — empty transcript. */
export async function GET(req: NextRequest) {
  if (isReadOnlyAgentDeploy()) {
    return NextResponse.json({ messages: [] });
  }
  return proxyAgentApi(req, "history");
}
