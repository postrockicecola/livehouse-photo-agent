import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isLandingOnly, isShowcase } from "@/lib/dataSource";

export function isReadOnlyAgentDeploy(): boolean {
  return isShowcase() || isLandingOnly();
}

/** Proxy Agent API to FastAPI in full mode. */
export async function proxyAgentApi(req: NextRequest, pathSuffix: string): Promise<NextResponse> {
  const origin = galleryApiOrigin();
  const url = `${origin}/api/agent/${pathSuffix}${req.nextUrl.search}`;
  const init: RequestInit = {
    method: req.method,
    cache: "no-store",
    headers: {
      "content-type": req.headers.get("content-type") ?? "application/json",
      accept: req.headers.get("accept") ?? "application/json",
    },
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }
  try {
    const res = await fetch(url, init);
    const contentType = res.headers.get("content-type") ?? "application/json";
    if (contentType.toLowerCase().includes("text/event-stream")) {
      return new NextResponse(res.body, {
        status: res.status,
        headers: {
          "content-type": contentType,
          "cache-control": res.headers.get("cache-control") ?? "no-cache, no-transform",
          "x-accel-buffering": "no",
        },
      });
    }
    const body = await res.text();
    return new NextResponse(body, {
      status: res.status,
      headers: {
        "content-type": contentType,
      },
    });
  } catch {
    return NextResponse.json({ detail: "agent backend unavailable" }, { status: 502 });
  }
}
