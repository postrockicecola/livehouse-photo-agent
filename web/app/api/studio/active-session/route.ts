import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin, runStudioCli } from "@/lib/studioPyRunner";

export const dynamic = "force-dynamic";

export async function PUT(req: NextRequest) {
  let previews_dir = "";
  try {
    const body = (await req.json()) as { previews_dir?: string };
    previews_dir = String(body.previews_dir || "").trim();
  } catch {
    return NextResponse.json({ detail: "invalid JSON body" }, { status: 400 });
  }
  if (!previews_dir) {
    return NextResponse.json({ detail: "previews_dir is required" }, { status: 400 });
  }

  try {
    const data = await runStudioCli<Record<string, unknown>>("set-active", [previews_dir]);
    return NextResponse.json(data);
  } catch {
    try {
      const origin = galleryApiOrigin();
      const res = await fetch(`${origin}/api/studio/active-session`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ previews_dir }),
      });
      if (res.ok) {
        return NextResponse.json(await res.json());
      }
    } catch {
      /* ignore */
    }
    return NextResponse.json({ detail: "set active session failed" }, { status: 500 });
  }
}
