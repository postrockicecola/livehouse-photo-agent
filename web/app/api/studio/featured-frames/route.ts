import { NextRequest, NextResponse } from "next/server";
import { runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase, loadFixture } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (isShowcase()) {
    return NextResponse.json(loadFixture("studio-featured-frames"));
  }

  const previewsDir = req.nextUrl.searchParams.get("previews_dir")?.trim();
  if (!previewsDir) {
    return NextResponse.json({ detail: "previews_dir is required" }, { status: 400 });
  }

  try {
    const data = await runStudioCli<Record<string, unknown>>("featured-frames", [previewsDir]);
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(loadFixture("studio-featured-frames"));
  }
}
