import { NextRequest, NextResponse } from "next/server";
import { isReadOnlyGalleryDeploy, proxyGalleryApi, showcaseReadOnlyJson } from "@/lib/galleryApiProxy";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    return showcaseReadOnlyJson(
      {
        detail: "Showcase 只读：口味画像重建需本地 gallery_server。",
        profile: null,
      },
      403,
    );
  }
  try {
    return await proxyGalleryApi(req, "api/gallery/taste/rebuild");
  } catch (e) {
    return NextResponse.json(
      { detail: e instanceof Error ? e.message : "taste rebuild unavailable" },
      { status: 502 },
    );
  }
}
