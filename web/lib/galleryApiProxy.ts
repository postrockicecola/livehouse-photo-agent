import { NextRequest, NextResponse } from "next/server";
import { isLandingOnly, isShowcase } from "@/lib/dataSource";
import { galleryApiOrigin } from "@/lib/studioPyRunner";

/** True when Gallery must not proxy to FastAPI. */
export function isReadOnlyGalleryDeploy(): boolean {
  return isShowcase() || isLandingOnly();
}

export async function proxyGalleryApi(req: NextRequest, upstreamPath: string): Promise<Response> {
  const origin = galleryApiOrigin();
  const url = new URL(`${origin}/${upstreamPath.replace(/^\//, "")}`);
  req.nextUrl.searchParams.forEach((v, k) => url.searchParams.set(k, v));

  const init: RequestInit = {
    method: req.method,
    cache: "no-store",
    headers: {
      accept: req.headers.get("accept") ?? "application/json",
      "content-type": req.headers.get("content-type") ?? "application/json",
    },
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
  }

  const res = await fetch(url, init);
  return new NextResponse(res.body, {
    status: res.status,
    headers: {
      "content-type": res.headers.get("content-type") ?? "application/json",
      "cache-control": "no-store",
    },
  });
}

export function showcaseReadOnlyJson(
  body: Record<string, unknown>,
  status = 200,
): NextResponse {
  return NextResponse.json(
    { ...body, showcase: true, read_only: true },
    { status },
  );
}
