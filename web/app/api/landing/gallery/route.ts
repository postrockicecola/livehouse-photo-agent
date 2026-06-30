import { NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";
import demoGallery from "@/lib/demoGallery.json";

export const dynamic = "force-dynamic";

export type LandingGalleryImage = {
  path: string;
};

export type LandingGalleryResponse = {
  export_dir: string;
  images: LandingGalleryImage[];
};

const DEFAULT_COUNT = 10;

// Bundled, EXIF-stripped demo images committed under web/public/demo/.
// Used when no live archive is reachable (fresh clone / Vercel deploy), so the
// landing page always has photos. Paths point to static assets (/demo/...).
function demoFallback(): LandingGalleryResponse {
  const images = Array.isArray((demoGallery as { images?: LandingGalleryImage[] }).images)
    ? (demoGallery as { images: LandingGalleryImage[] }).images
    : [];
  return { export_dir: "demo", images };
}

export async function GET() {
  try {
    const data = await runStudioCli<LandingGalleryResponse>("landing-gallery", [
      "--count",
      String(DEFAULT_COUNT),
    ]);
    if (data && Array.isArray(data.images) && data.images.length > 0) {
      return NextResponse.json(data);
    }
    return NextResponse.json(demoFallback());
  } catch {
    return NextResponse.json(demoFallback());
  }
}
