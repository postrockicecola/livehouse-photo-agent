import { NextRequest, NextResponse } from "next/server";
import { loadFixture } from "@/lib/dataSource";
import { isReadOnlyGalleryDeploy, proxyGalleryApi } from "@/lib/galleryApiProxy";
import type { GalleryShowcaseFixture } from "@/lib/galleryShowcase";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (isReadOnlyGalleryDeploy()) {
    const fixture = loadFixture<GalleryShowcaseFixture>("gallery-showcase-results");
    return NextResponse.json({
      build: "showcase",
      startup_base_dir: fixture.active_base_dir ?? "/showcase/agent-demo",
      active_base_dir: fixture.active_base_dir ?? "/showcase/agent-demo",
      results_json: "fixture:gallery-showcase-results",
      showcase: true,
      session_key: fixture.session_key ?? null,
      session_date: fixture.session_date ?? null,
    });
  }
  try {
    return await proxyGalleryApi(req, "api/debug/version");
  } catch {
    return NextResponse.json({
      build: "fallback",
      startup_base_dir: null,
      active_base_dir: null,
      results_json: null,
    });
  }
}
