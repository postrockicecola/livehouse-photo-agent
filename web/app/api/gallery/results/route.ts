import { NextRequest, NextResponse } from "next/server";
import { loadFixture } from "@/lib/dataSource";
import { isReadOnlyGalleryDeploy, proxyGalleryApi } from "@/lib/galleryApiProxy";
import {
  paginateGalleryShowcase,
  type GalleryShowcaseFixture,
} from "@/lib/galleryShowcase";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    const fixture = loadFixture<GalleryShowcaseFixture>("gallery-showcase-results");
    const sp = req.nextUrl.searchParams;
    return NextResponse.json(
      paginateGalleryShowcase(fixture, {
        offset: Number(sp.get("offset") || 0),
        limit: Number(sp.get("limit") || 120),
        sort: sp.get("sort") || "overall",
      }),
    );
  }
  try {
    return await proxyGalleryApi(req, "api/gallery/results");
  } catch {
    const fixture = loadFixture<GalleryShowcaseFixture>("gallery-showcase-results");
    return NextResponse.json(
      paginateGalleryShowcase(fixture, {
        offset: Number(req.nextUrl.searchParams.get("offset") || 0),
        limit: Number(req.nextUrl.searchParams.get("limit") || 120),
        sort: req.nextUrl.searchParams.get("sort") || "overall",
      }),
    );
  }
}
