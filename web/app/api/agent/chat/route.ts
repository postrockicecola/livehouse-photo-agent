import { NextRequest, NextResponse } from "next/server";
import { buildShowcaseAgentReply } from "@/lib/agentShowcase";
import { isReadOnlyAgentDeploy, proxyAgentApi } from "@/lib/agentApiProxy";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (isReadOnlyAgentDeploy()) {
    let message = "";
    try {
      const body = (await req.json()) as { message?: string };
      message = String(body?.message ?? "");
    } catch {
      message = "";
    }
    return NextResponse.json(buildShowcaseAgentReply(message));
  }
  return proxyAgentApi(req, "chat");
}
