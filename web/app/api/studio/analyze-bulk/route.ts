import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin } from "@/lib/studioPyRunner";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

/** Proxy FastAPI ``POST /api/studio/analyze-bulk`` (one job per session). */
export async function POST(req: NextRequest) {
  if (isShowcase()) {
    return NextResponse.json(
      { detail: "只读演示模式：Vercel 快照不运行批量分析（需本地后端 + GPU）" },
      { status: 403 },
    );
  }

  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    body = {};
  }

  const origin = galleryApiOrigin();
  try {
    const res = await fetch(`${origin}/api/studio/analyze-bulk`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return NextResponse.json(
        {
          detail:
            typeof data.detail === "string" ? data.detail : `analyze-bulk ${res.status}`,
        },
        { status: res.status },
      );
    }
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { detail: "analyze-bulk API unavailable (restart gallery_server?)" },
      { status: 502 },
    );
  }
}
