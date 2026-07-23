import { NextRequest, NextResponse } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi } from "@/lib/galleryApiProxy";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

/**
 * Showcase: no optical Lab — redirect to the static /showcase asset (or demo).
 * Full mode proxies to FastAPI film-render.
 */
export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy() || isShowcase()) {
    const path = req.nextUrl.searchParams.get("path") ?? "";
    const image = new URL("/image", req.url);
    image.searchParams.set("path", path);
    const max = req.nextUrl.searchParams.get("max_side");
    if (max) image.searchParams.set("max_side", max);
    const rot = req.nextUrl.searchParams.get("rotate");
    if (rot) image.searchParams.set("rotate", rot);
    return NextResponse.redirect(image);
  }
  try {
    return await proxyGalleryApi(req, "api/lab/film-render");
  } catch {
    return NextResponse.json({ detail: "film-render unavailable" }, { status: 502 });
  }
}
