import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

const DEMO_IMAGE_COUNT = 12; // web/public/demo/demo-01.jpg … demo-12.jpg

/** Deterministically map an arbitrary `path` to one of the bundled demo photos. */
function demoImageFor(path: string): string {
  let hash = 0;
  for (let i = 0; i < path.length; i += 1) {
    hash = (hash * 31 + path.charCodeAt(i)) >>> 0;
  }
  const n = (hash % DEMO_IMAGE_COUNT) + 1;
  return `/demo/demo-${String(n).padStart(2, "0")}.jpg`;
}

/**
 * Gallery image proxy. Locally this is normally served by a `next.config.js`
 * rewrite to FastAPI, but a Route Handler wins over rewrites, so we must also
 * proxy in full mode. In showcase mode there is no backend / real archive, so
 * we redirect to an EXIF-stripped bundled demo image.
 */
export async function GET(req: NextRequest) {
  const path = req.nextUrl.searchParams.get("path") ?? "";

  if (isShowcase()) {
    return NextResponse.redirect(new URL(demoImageFor(path), req.url));
  }

  try {
    const upstream = `${galleryApiOrigin()}/image?${req.nextUrl.searchParams.toString()}`;
    const res = await fetch(upstream, { cache: "no-store" });
    return new NextResponse(res.body, {
      status: res.status,
      headers: {
        "content-type": res.headers.get("content-type") ?? "image/jpeg",
        "cache-control": res.headers.get("cache-control") ?? "no-store",
      },
    });
  } catch {
    return NextResponse.redirect(new URL(demoImageFor(path), req.url));
  }
}
