import { NextResponse } from "next/server";
import { isShowcase, loadFixture } from "@/lib/dataSource";
import { runStudioCli } from "@/lib/studioPyRunner";
import demoGallery from "@/lib/demoGallery.json";

export const dynamic = "force-dynamic";

export type LandingGalleryImage = {
  path: string;
};

export type LandingGalleryResponse = {
  export_dir: string;
  images: LandingGalleryImage[];
  source?: string;
};

const DEFAULT_COUNT = 12;

type SessionRow = { cover_path_quoted?: string };

function coversFromSessions(sessions: SessionRow[] | undefined, count: number): LandingGalleryImage[] {
  const images: LandingGalleryImage[] = [];
  const seen = new Set<string>();
  for (const row of sessions ?? []) {
    const path = String(row.cover_path_quoted || "").trim();
    if (!path || seen.has(path)) continue;
    seen.add(path);
    images.push({ path });
    if (images.length >= count) break;
  }
  return images;
}

/** Same covers as the Studio session list (showcase fixtures / local archive). */
function sessionCoverFallback(): LandingGalleryResponse {
  try {
    const data = loadFixture("studio-sessions") as { sessions?: SessionRow[] };
    const images = coversFromSessions(data.sessions, DEFAULT_COUNT);
    if (images.length > 0) {
      return { export_dir: "session-covers", source: "session_covers", images };
    }
  } catch {
    /* fall through */
  }
  const images = Array.isArray((demoGallery as { images?: LandingGalleryImage[] }).images)
    ? (demoGallery as { images: LandingGalleryImage[] }).images
    : [];
  return { export_dir: "demo", source: "demo", images };
}

export async function GET() {
  if (isShowcase()) {
    return NextResponse.json(sessionCoverFallback());
  }
  try {
    const data = await runStudioCli<LandingGalleryResponse>("landing-gallery", [
      "--count",
      String(DEFAULT_COUNT),
    ]);
    if (data && Array.isArray(data.images) && data.images.length > 0) {
      return NextResponse.json({ ...data, source: data.source ?? "session_covers" });
    }
    return NextResponse.json(sessionCoverFallback());
  } catch {
    return NextResponse.json(sessionCoverFallback());
  }
}
