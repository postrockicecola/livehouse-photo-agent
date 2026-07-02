import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin, runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  // Showcase: no live pipeline — every session shares one representative snapshot.
  if (isShowcase()) {
    return NextResponse.json(loadFixture("studio-status"));
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
    return NextResponse.json(loadFixture("studio-status"));
  }
}
