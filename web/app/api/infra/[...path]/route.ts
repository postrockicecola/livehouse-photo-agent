import { NextRequest, NextResponse } from "next/server";
import {
  isReadOnlyInfraDeploy,
  proxyInfraBackend,
  serveInfraGet,
} from "@/lib/infraFixtureApi";

export const dynamic = "force-dynamic";

/**
 * Catch-all for the /infra console's FastAPI endpoints. Locally proxies to
 * gallery_server; on Vercel (showcase / landing-only) serves committed snapshots.
 * Job drill-downs also have explicit routes under jobs/[jobId]/* for reliability.
 */

export async function GET(req: NextRequest, { params }: { params: { path: string[] } }) {
  return serveInfraGet(req, params.path ?? []);
}

export async function POST(req: NextRequest, { params }: { params: { path: string[] } }) {
  if (isReadOnlyInfraDeploy()) {
    return NextResponse.json(
      { detail: "只读演示模式：Vercel 快照不支持重试/取消/暂停等写操作" },
      { status: 403 },
    );
  }
  return proxyInfraBackend(req, params.path ?? []);
}
