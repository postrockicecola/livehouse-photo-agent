import { NextRequest, NextResponse } from "next/server";
import { galleryApiOrigin, runStudioCli } from "@/lib/studioPyRunner";
import { isShowcase } from "@/lib/dataSource";

export const dynamic = "force-dynamic";

export async function GET() {
  if (isShowcase()) {
    return NextResponse.json({ config: {}, detail: "只读演示模式：使用默认摄取配置" });
  }
  try {
    const data = await runStudioCli<Record<string, unknown>>("ingest-config-get");
    return NextResponse.json(data);
  } catch {
    try {
      const res = await fetch(`${galleryApiOrigin()}/api/studio/ingest-config`, { cache: "no-store" });
      if (res.ok) return NextResponse.json(await res.json());
    } catch {
      /* ignore */
    }
    return NextResponse.json({ detail: "ingest-config unavailable" }, { status: 500 });
  }
}

export async function PUT(req: NextRequest) {
  if (isShowcase()) {
    return NextResponse.json(
      { detail: "只读演示模式：Vercel 快照不支持修改摄取配置" },
      { status: 403 },
    );
  }
  const body = await req.text();
  try {
    const data = await runStudioCli<Record<string, unknown>>("ingest-config-put", [body]);
    return NextResponse.json(data);
  } catch {
    try {
      const res = await fetch(`${galleryApiOrigin()}/api/studio/ingest-config`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body,
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        return NextResponse.json(
          { detail: typeof data.detail === "string" ? data.detail : "save failed" },
          { status: res.status },
        );
      }
      return NextResponse.json(data);
    } catch {
      return NextResponse.json({ detail: "ingest-config save failed" }, { status: 500 });
    }
  }
}
