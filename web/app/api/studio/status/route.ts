import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin, runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";
import { applyShowcaseStatusOverlay } from "@/lib/showcaseWorkflow";

export const dynamic = "force-dynamic";

type SessionsFixture = {
  active?: {
    session_key?: string;
    previews_dir?: string;
    preview_count?: number;
    photos_ingested?: number;
    brain_session_id?: number | null;
    session_date?: string;
    band_name?: string;
    has_analysis_results?: boolean;
    session_dir?: string;
    funnel?: Record<string, number | null>;
  } | null;
  sessions?: Array<{
    session_key?: string;
    session_dir?: string;
    previews_dir?: string;
    preview_count?: number;
    photos_ingested?: number;
    brain_session_id?: number | null;
    session_date?: string;
    band_name?: string;
    has_analysis_results?: boolean;
    funnel?: Record<string, number | null>;
  }>;
};

export async function GET(req: NextRequest) {
  // Showcase: rematerialize workflow counts from the selected catalog session
  // so Imported → Exported is a real taper, not a flat no-op line.
  if (isShowcase()) {
    const base = loadFixture<Record<string, unknown>>("studio-status");
    const catalog = loadFixture<SessionsFixture>("studio-sessions");
    const previewsDir = req.nextUrl.searchParams.get("previews_dir")?.trim();
    const row =
      (previewsDir
        ? catalog.sessions?.find((s) => s.previews_dir === previewsDir)
        : null) ??
      catalog.active ??
      catalog.sessions?.[0] ??
      null;
    return NextResponse.json(applyShowcaseStatusOverlay(base, row));
  }

  const previewsDir = req.nextUrl.searchParams.get("previews_dir")?.trim();
  if (!previewsDir) {
    return NextResponse.json({ detail: "previews_dir is required" }, { status: 400 });
  }

  try {
    const data = await runStudioCli<Record<string, unknown>>("status", [previewsDir]);
    return NextResponse.json(data);
  } catch {
    // Fallback: old FastAPI without /api/studio/status
    try {
      const origin = galleryApiOrigin();
      const q = new URLSearchParams({ previews_dir: previewsDir });
      const res = await fetch(`${origin}/api/studio/status?${q}`, { cache: "no-store" });
      if (res.ok) {
        return NextResponse.json(await res.json());
      }
    } catch {
      /* ignore */
    }
    const base = loadFixture<Record<string, unknown>>("studio-status");
    const catalog = loadFixture<SessionsFixture>("studio-sessions");
    const row =
      catalog.sessions?.find((s) => s.previews_dir === previewsDir) ??
      catalog.active ??
      null;
    return NextResponse.json(applyShowcaseStatusOverlay(base, row));
  }
}
